# web_bridge.py - 시연 담당: 수경이 FastAPI 서버에 붙이는 부분
#
#  [ESP32] ─ws─ [FastAPI: 수경, 5000] ═TCP 5002═ [코틀린 웹 서버: 형진, 5001] ─ 브라우저
#                  │                    (시연)
#               vision.py (웹캠 빨간 점 세기)
#
# 수경이 main.py에 추가하는 건 이것뿐 ([시연 추가] 표시):
#   bridge = WebBridge(app, zone_status, esp32_clients, save_zone_status,
#                      make_display_command, HISTORY_FILE)
#   bridge.on_pass(sensor_data)                 ← ESP32 이동 감지할 때
#   bridge.note_status(zone, status, count)     ← POST /zone-status 받을 때
#
# 서버 간 통신 (TCP 5002, tcp_link.py 규격)
#   FastAPI → 코틀린 : {"type":"status","data":{화면용 전체 상태}}   1초마다, ack 필요
#   코틀린 → FastAPI : {"type":"hello","role":"web"}
#                      {"type":"control","action":"restrict","zone":"A"}  ack 필요
#   (+ 공통: ack, heartbeat)
#
# 브라우저가 FastAPI에 직접 붙어도 되게 /admin, /m, /api/status, /ws/status, /api/control 도 만들어 둠
import asyncio
import json
import threading
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


class ControlError(Exception):
    pass


def judge(count, flow, capacity):
    """혼잡 판단 규칙 (인원 + 30초 순유입)"""
    if count >= capacity or (count == capacity - 1 and flow >= 2):
        return "CROWDED"
    if count == capacity - 1 or flow >= 2:
        return "CAUTION"
    return "NORMAL"


class WebBridge:
    def __init__(self, app, zone_status, esp32_clients, save_zone_status,
                 make_display_command, history_file=None, capacity=4, link_port=5002):
        self.zone_status = zone_status            # 수경이 데이터 그대로 참조
        self.esp32_clients = esp32_clients
        self.save_zone_status = save_zone_status
        self.make_display_command = make_display_command
        self.capacity = capacity

        self.counts = {}                          # 구역별 인원 (vision.py 또는 AI가 알려줌)
        self.history = {z: deque(maxlen=60) for z in ZONES}
        self.events = deque(maxlen=300)           # ToF 통과 기록
        self.logs = deque(maxlen=40)
        self.restricted = set()
        self.message = None
        self.loop = None

        @app.on_event("startup")
        async def _capture_loop():                # ESP32로 보낼 때 쓰는 이벤트 루프 기억
            self.loop = asyncio.get_running_loop()

        self.link = LinkServer(link_port, self._on_link_message)
        self.link.start()
        threading.Thread(target=self._push_loop, daemon=True).start()
        self._routes(app)

    # ============================================================
    # 1) 수경이 코드 / vision.py 에서 부르는 함수
    # ============================================================
    def on_pass(self, sensor_data):
        """ESP32 이동 감지 → 기록"""
        a, b = sensor_data["from_zone"], sensor_data["to_zone"]
        gate = GATE_OF.get(frozenset((a, b)), "?")
        self.events.append({"ts": time.time(), "gate": gate, "from": a, "to": b})
        self.log("pass", f"{gate} {a}→{b}")

    def note_status(self, zone, status, count=None):
        """POST /zone-status 로 상태가 바뀜 (테스트용 경로)"""
        if count is not None:
            self.counts[zone] = count
        self.log("status", f"{zone}={status} (HTTP)")

    def set_counts(self, counts):
        """vision.py가 웹캠으로 센 인원을 알려줌 → 판단 → 바뀌면 ESP32에 명령"""
        now = time.time()
        changed = []
        for z, c in counts.items():
            if z not in self.zone_status:
                continue
            self.counts[z] = c
            level = judge(c, self._flow(z, now), self.capacity)
            if self.zone_status[z] != level:
                self.zone_status[z] = level
                changed.append(z)
        if changed:
            self.save_zone_status()
            self.log("status", " ".join(f"{z}={self.zone_status[z]}" for z in changed))
            for z in changed:
                if z not in self.restricted:
                    self._to_esp32_threadsafe(self.make_display_command(z, self.zone_status[z]))

    # ============================================================
    # 2) 서버 간 통신: 코틀린 웹 서버
    # ============================================================
    def _push_loop(self):
        """1초마다 코틀린 서버로 전체 상태 전송 (ack로 응답 시간 측정)"""
        while True:
            time.sleep(1)
            for z in ZONES:
                self.history[z].append(self.counts.get(z, 0))
            self.link.send({"type": "status", "data": self.status()}, reliable=True)

    def _on_link_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            self.log("link", f"{msg.get('role', '?')} 서버 접속")
        elif kind == "control":
            if not self.loop:
                return
            fut = asyncio.run_coroutine_threadsafe(self.apply_control(msg), self.loop)
            try:
                fut.result(timeout=5)
            except Exception as e:
                self.log("control", f"코틀린 제어 실패: {e}")

    # ============================================================
    # 3) 관리자 제어 (브라우저 → FastAPI 직접, 또는 코틀린 → TCP 둘 다 여기로)
    # ============================================================
    async def apply_control(self, c):
        action, zone, text = c.get("action"), c.get("zone"), c.get("text")
        if action in ("restrict", "open"):
            if zone not in ZONES:
                raise ControlError("구역은 A, B, C 중 하나여야 해요")
            if action == "restrict":
                self.restricted.add(zone)
                await self._to_esp32({"type": "DISPLAY", "zone": zone, "status": "CLOSED",
                                      "lcd_message": f"{zone} ZONE CLOSED", "led_pattern": "CLOSED"})
            else:
                self.restricted.discard(zone)
                await self._to_esp32(self.make_display_command(zone, self.zone_status[zone]))
            self.log("control", f"관리자 · {zone} 입장 {'제한' if action == 'restrict' else '해제'}")
        elif action == "message":
            t = (text or "").strip().upper()
            if not t or len(t) > 20 or not all(32 <= ord(ch) < 127 for ch in t):
                raise ControlError("LCD 문구는 영문·숫자 20자 이내로 입력해 주세요")
            self.message = t
            await self._to_esp32({"type": "MESSAGE", "lcd_message": t})
            self.log("control", f"관리자 · LCD \"{t}\"")
        elif action == "auto":
            self.restricted.clear()
            self.message = None
            for z in ZONES:
                await self._to_esp32(self.make_display_command(z, self.zone_status[z]))
            self.log("control", "관리자 · 자동 안내로 복귀")
        else:
            raise ControlError("알 수 없는 명령이에요")

    # ============================================================
    # ESP32로 명령 보내기 (수경이 서버의 WebSocket 목록 사용)
    # ============================================================
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

    # ============================================================
    # 화면용 상태
    # ============================================================
    def log(self, kind, text):
        self.logs.appendleft({"ts": time.time(), "kind": kind, "text": text})

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
            zones.append({"id": z, "count": self.counts.get(z, 0), "count_known": z in self.counts,
                          "capacity": self.capacity, "flow_30s": self._flow(z, now),
                          "level": self.zone_status[z].lower(), "restricted": z in self.restricted,
                          "history": list(self.history[z])})
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
                "web_server_connected": ls.get("connected", False),
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

    # ============================================================
    # 브라우저가 FastAPI에 직접 붙을 때 쓰는 주소 (테스트·백업용)
    # ============================================================
    def _routes(self, app):
        bridge = self

        @app.get("/admin")
        def admin_page():
            return FileResponse(STATIC / "admin.html")

        @app.get("/m")
        def mobile_page():
            return FileResponse(STATIC / "mobile.html")

        @app.get("/api/status")
        def api_status():
            return bridge.status()

        @app.websocket("/ws/status")
        async def ws_status(ws: WebSocket):
            await ws.accept()
            try:
                while True:
                    await ws.send_json(bridge.status())
                    await asyncio.sleep(1)
            except WebSocketDisconnect:
                pass

        @app.post("/api/control")
        async def control(c: Control):
            try:
                await bridge.apply_control(c.model_dump())
            except ControlError as e:
                raise HTTPException(400, str(e))
            return bridge.status()
