# web_bridge.py - 시연 담당: 수경이 FastAPI 서버에 붙이는 "웹 화면 + 서버 간 통신" 부분
#
# 수경이 main.py에 추가하는 건 이것뿐:
#   from web_bridge import WebBridge
#   bridge = WebBridge(app, zone_status, esp32_clients, save_zone_status,
#                      make_display_command, HISTORY_FILE)
#   (+ ESP32 이동 감지 부분에 bridge.on_pass(sensor_data) 한 줄,
#    + /zone-status POST 부분에 bridge.note_status(...) 한 줄)
#
# 이 파일이 만드는 주소
#   GET  /admin       관리자 화면 (PC)
#   GET  /m           관객 화면 (모바일)
#   GET  /api/status  화면용 상태 JSON 한 번 조회
#   WS   /ws/status   화면용 상태 JSON 1초마다 푸시
#   POST /api/control 관리자 제어 (입장 제한, LCD 문구, 자동 복귀)
#   TCP  5002         AI 서버(형진)와 서버 간 통신 (tcp_link.py, 시연 요소기술)
import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tcp_link import LinkServer

STATIC = Path(__file__).with_name("static")
ZONES = ["A", "B", "C"]
GATE_OF = {frozenset("AB"): "G1", frozenset("BC"): "G2"}
GATES = {"G1": ("A", "B"), "G2": ("B", "C")}


class Control(BaseModel):
    action: str                    # "restrict" | "open" | "message" | "auto"
    zone: Optional[str] = None
    text: Optional[str] = None


class WebBridge:
    def __init__(self, app, zone_status, esp32_clients, save_zone_status,
                 make_display_command, history_file=None, capacity=4, link_port=5002):
        # 수경이 서버의 데이터를 그대로 참조 (복사 아님)
        self.zone_status = zone_status
        self.esp32_clients = esp32_clients
        self.save_zone_status = save_zone_status
        self.make_display_command = make_display_command
        self.capacity = capacity

        self.counts = {}                                  # AI가 알려준 구역별 인원
        self.history = {z: deque(maxlen=60) for z in ZONES}
        self.events = deque(maxlen=300)                   # ToF 통과 기록
        self.logs = deque(maxlen=40)
        self.restricted = set()
        self.message = None
        self.loop = None                                  # FastAPI 이벤트 루프 (ESP32 전송용)
        self._load_history(history_file)

        # 서버 간 통신: AI 서버가 여기로 접속 (시연 tcp_link.py)
        self.link = LinkServer(link_port, self._on_ai_message)
        self.link.start()
        self._routes(app)

    # ------------------------------------------------------------
    # 수경이 코드에서 부르는 함수 2개
    # ------------------------------------------------------------
    def on_pass(self, sensor_data):
        """ESP32에서 이동이 감지될 때마다 호출 → 화면 기록 + AI 서버로 전달"""
        a, b = sensor_data["from_zone"], sensor_data["to_zone"]
        gate = GATE_OF.get(frozenset((a, b)), "?")
        self.events.append({"ts": time.time(), "gate": gate, "from": a, "to": b})
        self.log("pass", f"{gate} {a}→{b}")
        self.link.send({"type": "pass", "gate": gate, "from": a, "to": b}, reliable=True)

    def note_status(self, zone, status, count=None):
        """HTTP POST /zone-status로 상태가 바뀌었을 때 호출 (테스트용 경로)"""
        if count is not None:
            self.counts[zone] = count
        self.log("status", f"{zone}={status} (HTTP)")

    # ------------------------------------------------------------
    # AI 서버(형진)에서 TCP로 온 메시지 처리 - 다른 스레드에서 실행됨
    # ------------------------------------------------------------
    def _on_ai_message(self, msg):
        if msg.get("type") == "hello":
            self.log("link", "AI 서버 접속")
            return
        if msg.get("type") != "status":
            return
        changed = []
        for z, info in msg.get("zones", {}).items():
            if z not in self.zone_status:
                continue
            if "count" in info:
                self.counts[z] = info["count"]
            level = str(info.get("level", "")).upper()
            if level in ("NORMAL", "CAUTION", "CROWDED") and self.zone_status[z] != level:
                self.zone_status[z] = level
                changed.append(z)
        if changed:
            self.save_zone_status()
            self.log("status", " ".join(f"{z}={self.zone_status[z]}" for z in changed) + " (AI)")
            for z in changed:
                if z not in self.restricted:
                    self._to_esp32_threadsafe(self.make_display_command(z, self.zone_status[z]))

    # ------------------------------------------------------------
    # ESP32로 명령 보내기 (수경이 서버의 WebSocket 목록 사용)
    # ------------------------------------------------------------
    async def _to_esp32(self, command):
        for client in list(self.esp32_clients):
            try:
                await client.send_json(command)
            except Exception:
                self.esp32_clients.discard(client)
        self.log("display", f"→ ESP32 {command.get('zone', '')} {command.get('status', command.get('lcd_message', ''))}")

    def _to_esp32_threadsafe(self, command):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._to_esp32(command), self.loop)

    # ------------------------------------------------------------
    # 화면용 상태 만들기
    # ------------------------------------------------------------
    def log(self, kind, text):
        self.logs.appendleft({"ts": time.time(), "kind": kind, "text": text})

    def _load_history(self, history_file):
        """서버 시작 전 기록은 화면 로그에만 몇 개 보여줌"""
        if not history_file or not Path(history_file).exists():
            return
        try:
            lines = Path(history_file).read_text(encoding="utf-8").strip().splitlines()[-5:]
            for line in lines:
                r = json.loads(line)
                self.logs.append({"ts": time.time(), "kind": "pass",
                                  "text": f"(이전 기록) {r['from_zone']}→{r['to_zone']}"})
        except Exception:
            pass

    def _flow(self, zone, now):
        recent = [e for e in self.events if now - e["ts"] <= 30]
        return sum(e["to"] == zone for e in recent) - sum(e["from"] == zone for e in recent)

    def _gate(self, gid, now):
        recent = [e for e in self.events if e["gate"] == gid and now - e["ts"] <= 30]
        last = next((e for e in reversed(self.events) if e["gate"] == gid), None)
        return {"id": gid, "between": list(GATES[gid]), "passes_30s": len(recent),
                "last": {"from": last["from"], "to": last["to"], "ago": round(now - last["ts"], 1)} if last else None}

    def status(self):
        now = time.time()
        zones = []
        for z in ZONES:
            known = z in self.counts
            c = self.counts.get(z, 0)
            zones.append({"id": z, "count": c, "count_known": known, "capacity": self.capacity,
                          "flow_30s": self._flow(z, now), "level": self.zone_status[z].lower(),
                          "restricted": z in self.restricted, "history": list(self.history[z])})
        busy = sorted([z for z in zones if z["level"] != "normal"],
                      key=lambda z: (z["level"] != "crowded", -z["count"]))
        free = sorted([z for z in zones if z["level"] == "normal" and not z["restricted"]],
                      key=lambda z: z["count"])
        worst = busy[0]["id"] if busy else None
        rec = free[0]["id"] if free else None
        ls = self.link.stats()
        last_pass = self.events[-1]["ts"] if self.events else None
        return {
            "ts": now,
            "device": {"online": len(self.esp32_clients) > 0},
            "zones": zones,
            "gates": [self._gate(g, now) for g in GATES],
            "worst": worst,
            "recommend": rec,
            "control": {"restricted": sorted(self.restricted), "message": self.message},
            "lcd": self._lcd(zones, worst, rec),
            "comm": {
                "heartbeat_ago": round(now - last_pass) if last_pass else None,
                "last_ms": ls.get("rtt_last_ms"), "avg_ms": ls.get("rtt_avg_ms"),
                "max_ms": ls.get("rtt_max_ms"), "retries": ls.get("retries"),
                "recent_ms": list(self.link.link.rtt_ms)[-20:] if self.link.link else [],
                "ai_connected": ls.get("connected", False),
            },
            "logs": list(self.logs)[:14],
        }

    def _lcd(self, zones, worst, rec):
        l1 = " ".join(f"{z['id']}{z['count'] if z['count_known'] else '-'}/{z['capacity']}" for z in zones)
        if self.restricted:
            l2 = "ZONE " + ",".join(sorted(self.restricted)) + " CLOSED"
        elif worst:
            l2 = f"ZONE {worst} " + ("FULL" if self.zone_status[worst] == "CROWDED" else "BUSY")
        else:
            l2 = "ALL ZONES OK"
        if self.message:
            l3 = self.message
        elif worst or self.restricted:
            l3 = f"PLEASE USE ZONE {rec}" if rec else "PLEASE WAIT"
        else:
            l3 = "ENJOY THE SHOW"
        l4 = "SERVER LINK OK" if self.esp32_clients else "ESP32 OFFLINE"
        return [s[:20] for s in (l1, l2, l3, l4)]

    # ------------------------------------------------------------
    # 주소(라우트) 등록
    # ------------------------------------------------------------
    def _routes(self, app):
        bridge = self

        @app.get("/admin")
        async def admin_page():
            bridge.loop = asyncio.get_running_loop()
            return FileResponse(STATIC / "admin.html")

        @app.get("/m")
        async def mobile_page():
            bridge.loop = asyncio.get_running_loop()
            return FileResponse(STATIC / "mobile.html")

        @app.get("/api/status")
        async def api_status():
            bridge.loop = asyncio.get_running_loop()
            return bridge.status()

        @app.websocket("/ws/status")
        async def ws_status(ws: WebSocket):
            bridge.loop = asyncio.get_running_loop()
            await ws.accept()
            try:
                while True:
                    for z in ZONES:                       # 60초 그래프용 기록
                        bridge.history[z].append(bridge.counts.get(z, 0))
                    await ws.send_json(bridge.status())
                    await asyncio.sleep(1)
            except WebSocketDisconnect:
                pass

        @app.post("/api/control")
        async def control(c: Control):
            bridge.loop = asyncio.get_running_loop()
            if c.action in ("restrict", "open"):
                if c.zone not in ZONES:
                    raise HTTPException(400, "구역은 A, B, C 중 하나여야 해요")
                if c.action == "restrict":
                    bridge.restricted.add(c.zone)
                    await bridge._to_esp32({"type": "DISPLAY", "zone": c.zone, "status": "CLOSED",
                                            "lcd_message": f"{c.zone} ZONE CLOSED", "led_pattern": "CLOSED"})
                else:
                    bridge.restricted.discard(c.zone)
                    await bridge._to_esp32(bridge.make_display_command(c.zone, bridge.zone_status[c.zone]))
                bridge.log("control", f"관리자 · {c.zone} 입장 {'제한' if c.action == 'restrict' else '해제'}")
            elif c.action == "message":
                text = (c.text or "").strip().upper()
                if not text or len(text) > 20 or not all(32 <= ord(ch) < 127 for ch in text):
                    raise HTTPException(400, "LCD 문구는 영문·숫자 20자 이내로 입력해 주세요")
                bridge.message = text
                await bridge._to_esp32({"type": "MESSAGE", "lcd_message": text})
                bridge.log("control", f"관리자 · LCD \"{text}\"")
            elif c.action == "auto":
                bridge.restricted.clear()
                bridge.message = None
                for z in ZONES:
                    await bridge._to_esp32(bridge.make_display_command(z, bridge.zone_status[z]))
                bridge.log("control", "관리자 · 자동 안내로 복귀")
            else:
                raise HTTPException(400, "알 수 없는 명령이에요")
            return bridge.status()
