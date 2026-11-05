# dashboard_bridge.py
# 관리자·관객 화면 및 Kotlin 서버 연결

import asyncio
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server_link_tcp import LinkServer


STATIC = Path(__file__).with_name("static")

ZONES = ["A", "B", "C"]
GATE_OF = {
    frozenset(("A", "B")): "G1",
    frozenset(("B", "C")): "G2",
}
GATES = {
    "G1": ("A", "B"),
    "G2": ("B", "C"),
}

# 모형 시연용 기준
DEFAULT_CAPACITY = 15
CAUTION_RATIO = 0.60
CROWDED_RATIO = 0.80


class Control(BaseModel):
    action: str
    zone: Optional[str] = None
    text: Optional[str] = None


class ControlError(Exception):
    pass


def judge(count, flow, capacity):
    """정원 15명 기준: 9명부터 주의, 12명부터 혼잡."""
    ratio = count / capacity

    if ratio >= CROWDED_RATIO:
        return "CROWDED"
    if ratio >= CAUTION_RATIO:
        return "CAUTION"
    return "NORMAL"


class WebBridge:
    def __init__(
            self,
            app,
            zone_status,
            esp32_clients,
            save_zone_status,
            make_display_command,
            history_file=None,
            capacity=DEFAULT_CAPACITY,
            link_port=5002,
    ):
        if capacity <= 0:
            raise ValueError("수용인원은 1 이상이어야 합니다.")

        self.zone_status = zone_status
        self.esp32_clients = esp32_clients
        self.save_zone_status = save_zone_status
        self.make_display_command = make_display_command
        self.capacity = capacity

        self.counts = {}
        self.history = {
            z: deque(maxlen=60) for z in ZONES
        }
        self.events = deque(maxlen=300)
        self.logs = deque(maxlen=40)
        self.restricted = set()
        self.message = None
        self.loop = None

        # 카메라·TCP·웹 요청 사이의 데이터 접근 보호
        self.lock = threading.RLock()

        @app.on_event("startup")
        async def _capture_loop():
            self.loop = asyncio.get_running_loop()

            # 시작 전에 카메라가 확정한 상태도 장치에 전달
            with self.lock:
                commands = [
                    self.make_display_command(z, self.zone_status[z])
                    for z in ZONES
                    if z in self.counts
                ]

            for command in commands:
                await self._to_esp32(command)

        self.link = LinkServer(
            link_port, self._on_link_message
        )
        self.link.start()

        self._routes(app)

        threading.Thread(
            target=self._push_loop,
            daemon=True,
        ).start()

    # =====================================
    # 1. 카메라·센서 데이터
    # =====================================

    def on_pass(self, sensor_data):
        """센서 이동 기록은 흐름 통계에 사용."""
        a = sensor_data["from_zone"]
        b = sensor_data["to_zone"]
        gate = GATE_OF.get(frozenset((a, b)), "?")

        with self.lock:
            self.events.append({
                "ts": time.time(),
                "gate": gate,
                "from": a,
                "to": b,
            })

        self.log("pass", f"{gate} {a}→{b}")

    def note_status(self, zone, status, count=None):
        """HTTP 테스트 경로에서 변경한 상태 기록."""
        with self.lock:
            if count is not None:
                self.counts[zone] = max(0, int(count))

        self.log("status", f"{zone}={status} (HTTP)")

    def set_counts(self, counts):
        """카메라 인원으로 혼잡 상태 판단."""
        now = time.time()
        commands = []
        changed = []

        with self.lock:
            for z, c in counts.items():
                if z not in ZONES:
                    continue

                # 정원을 초과해도 실제 집계 인원 유지
                c = max(0, int(c))
                self.counts[z] = c

                level = judge(
                    c, self._flow(z, now), self.capacity
                )

                if self.zone_status[z] != level:
                    self.zone_status[z] = level
                    changed.append(z)

                    if z not in self.restricted:
                        commands.append(
                            self.make_display_command(z, level)
                        )

            if changed:
                self.save_zone_status()
                self.log(
                    "status",
                    " ".join(
                        f"{z}={self.zone_status[z]}"
                        for z in changed
                    ),
                )

        for command in commands:
            self._to_esp32_threadsafe(command)

    # =====================================
    # 2. Kotlin 서버 통신
    # =====================================

    def _push_loop(self):
        """1초마다 기록 및 전체 상태 전송."""
        while True:
            time.sleep(1)

            try:
                with self.lock:
                    for z in ZONES:
                        self.history[z].append(
                            self.counts.get(z, 0)
                        )

                self.link.send(
                    {
                        "type": "status",
                        "data": self.status(),
                    },
                    reliable=True,
                )
            except Exception as e:
                self.log("link", f"상태 전송 실패: {e}")

    def _on_link_message(self, msg):
        kind = msg.get("type")

        if kind == "hello":
            self.log(
                "link",
                f"{msg.get('role', '?')} 서버 접속",
            )

        elif kind == "control":
            if self.loop is None:
                return

            future = asyncio.run_coroutine_threadsafe(
                self.apply_control(msg),
                self.loop,
            )

            try:
                future.result(timeout=5)
            except Exception as e:
                self.log(
                    "control",
                    f"Kotlin 제어 실패: {e}",
                )

    # =====================================
    # 3. 관리자 제어
    # =====================================

    async def apply_control(self, c):
        action = c.get("action")
        zone = c.get("zone")
        text = c.get("text")

        if action in ("restrict", "open"):
            if zone not in ZONES:
                raise ControlError(
                    "구역은 A, B, C 중 하나여야 해요"
                )

            with self.lock:
                if action == "restrict":
                    self.restricted.add(zone)
                    command = {
                        "type": "DISPLAY",
                        "zone": zone,
                        "status": "CLOSED",
                        "lcd_message": f"{zone} ZONE CLOSED",
                        "led_pattern": "CLOSED",
                    }
                else:
                    self.restricted.discard(zone)
                    command = self.make_display_command(
                        zone, self.zone_status[zone]
                    )

            await self._to_esp32(command)

            label = "제한" if action == "restrict" else "해제"
            self.log(
                "control",
                f"관리자 · {zone} 입장 {label}",
            )

        elif action == "message":
            t = (text or "").strip().upper()

            if (
                    not t
                    or len(t) > 20
                    or not all(32 <= ord(ch) < 127 for ch in t)
            ):
                raise ControlError(
                    "LCD 문구는 영문·숫자 20자 이내로 입력해 주세요"
                )

            with self.lock:
                self.message = t

            await self._to_esp32({
                "type": "MESSAGE",
                "lcd_message": t,
            })
            self.log("control", f'관리자 · LCD "{t}"')

        elif action == "auto":
            with self.lock:
                self.restricted.clear()
                self.message = None
                commands = [
                    self.make_display_command(
                        z, self.zone_status[z]
                    )
                    for z in ZONES
                ]

            for command in commands:
                await self._to_esp32(command)

            self.log("control", "관리자 · 자동 안내로 복귀")

        else:
            raise ControlError("알 수 없는 명령이에요")

    # =====================================
    # 4. ESP32 명령 전송
    # =====================================

    async def _to_esp32(self, command):
        for client in list(self.esp32_clients):
            try:
                await client.send_json(command)
            except Exception:
                self.esp32_clients.discard(client)

        detail = command.get(
            "status", command.get("lcd_message", "")
        )
        self.log(
            "display",
            f"→ ESP32 {command.get('zone', '')} {detail}",
        )

    def _to_esp32_threadsafe(self, command):
        if self.loop is not None and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._to_esp32(command),
                self.loop,
            )

    # =====================================
    # 5. 화면 데이터
    # =====================================

    def log(self, kind, text):
        with self.lock:
            self.logs.appendleft({
                "ts": time.time(),
                "kind": kind,
                "text": text,
            })

    def _flow(self, zone, now):
        with self.lock:
            recent = [
                e for e in self.events
                if now - e["ts"] <= 30
            ]

        incoming = sum(e["to"] == zone for e in recent)
        outgoing = sum(e["from"] == zone for e in recent)
        return incoming - outgoing

    def _gate(self, gid, now):
        with self.lock:
            events = list(self.events)

        recent = [
            e for e in events
            if e["gate"] == gid and now - e["ts"] <= 30
        ]
        last = next(
            (
                e for e in reversed(events)
                if e["gate"] == gid
            ),
            None,
        )

        return {
            "id": gid,
            "between": list(GATES[gid]),
            "passes_30s": len(recent),
            "last": {
                "from": last["from"],
                "to": last["to"],
                "ago": round(now - last["ts"], 1),
            } if last else None,
        }

    def status(self):
        now = time.time()

        with self.lock:
            zones = []

            for z in ZONES:
                count = self.counts.get(z, 0)
                zones.append({
                    "id": z,
                    "count": count,
                    "count_known": z in self.counts,
                    "capacity": self.capacity,
                    "flow_30s": self._flow(z, now),
                    "level": self.zone_status[z].lower(),
                    "restricted": z in self.restricted,
                    "history": list(self.history[z]),
                    "at_capacity": count >= self.capacity,
                    "over_capacity": count > self.capacity,
                    "excess_count": max(
                        0, count - self.capacity
                    ),
                })

            busy = sorted(
                [
                    z for z in zones
                    if z["level"] != "normal"
                ],
                key=lambda z: (
                    z["level"] != "crowded",
                    -z["count"],
                ),
            )

            free = sorted(
                [
                    z for z in zones
                    if (
                        z["count_known"]
                        and z["level"] == "normal"
                        and not z["restricted"]
                        and not z["at_capacity"]
                )
                ],
                key=lambda z: z["count"],
            )

            worst = busy[0]["id"] if busy else None
            recommend = free[0]["id"] if free else None

            last_pass = (
                self.events[-1]["ts"]
                if self.events else None
            )

            control = {
                "restricted": sorted(self.restricted),
                "message": self.message,
            }
            lcd = self._lcd(zones, worst, recommend)
            logs = list(self.logs)[:14]

        link_stats = self.link.stats()
        connection = self.link.link

        return {
            "ts": now,
            "device": {
                "online": len(self.esp32_clients) > 0
            },
            "zones": zones,
            "gates": [
                self._gate(g, now) for g in GATES
            ],
            # 기존 웹 화면과 호환되는 대표 구역
            "worst": worst,
            "recommend": recommend,
            # 여러 구역 상태 표시용 추가 정보
            "busy_zones": [z["id"] for z in busy],
            "full_zones": [
                z["id"] for z in zones if z["at_capacity"]
            ],
            "over_capacity_zones": [
                z["id"] for z in zones if z["over_capacity"]
            ],
            "control": control,
            "lcd": lcd,
            "comm": {
                "heartbeat_ago": (
                    round(now - last_pass)
                    if last_pass is not None else None
                ),
                "last_ms": link_stats.get("rtt_last_ms"),
                "avg_ms": link_stats.get("rtt_avg_ms"),
                "max_ms": link_stats.get("rtt_max_ms"),
                "retries": link_stats.get("retries"),
                "recent_ms": (
                    list(connection.rtt_ms)[-20:]
                    if connection else []
                ),
                "web_server_connected": link_stats.get(
                    "connected", False
                ),
            },
            "logs": logs,
        }

    def _lcd(self, zones, worst, rec):
        # 15명씩 표시해도 20자에 들어가도록 구성
        l1 = " ".join(
            f"{z['id']}:{z['count'] if z['count_known'] else '-'}"
            for z in zones
        )

        over = [
            z["id"] for z in zones if z["over_capacity"]
        ]
        full = [
            z["id"] for z in zones if z["at_capacity"]
        ]
        crowded = [
            z["id"] for z in zones
            if z["level"] == "crowded"
        ]
        caution = [
            z["id"] for z in zones
            if z["level"] == "caution"
        ]

        if self.restricted:
            l2 = "ZONE " + ",".join(
                sorted(self.restricted)
            ) + " CLOSED"
        elif over:
            l2 = "ZONE " + ",".join(over) + " OVER CAP"
        elif full:
            l2 = "ZONE " + ",".join(full) + " FULL"
        elif crowded:
            l2 = "ZONE " + ",".join(crowded) + " CROWDED"
        elif caution:
            l2 = "ZONE " + ",".join(caution) + " BUSY"
        else:
            l2 = "ALL ZONES OK"

        if self.message:
            l3 = self.message
        elif worst or self.restricted:
            l3 = (
                f"PLEASE USE ZONE {rec}"
                if rec else "PLEASE WAIT"
            )
        else:
            l3 = "ENJOY THE SHOW"

        l4 = (
            "SERVER LINK OK"
            if self.esp32_clients else "ESP32 OFFLINE"
        )

        return [s[:20] for s in (l1, l2, l3, l4)]

    # =====================================
    # 6. FastAPI 웹 경로
    # =====================================

    def _routes(self, app):
        bridge = self

        @app.get("/manager")
        def manager_page():
            return FileResponse(STATIC / "manager.html")

        @app.get("/m")
        def audience_page():
            return FileResponse(STATIC / "audience.html")

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
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

            return bridge.status()