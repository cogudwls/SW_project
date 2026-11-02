
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

app = FastAPI()


# =====================================
# 1. 혼잡 상태 저장 및 불러오기
# =====================================

STATUS_FILE = Path(__file__).with_name("zone_status.json")

# 기본 혼잡 상태
DEFAULT_STATUS = {
    "A": "NORMAL",
    "B": "NORMAL",
    "C": "NORMAL"
}


# 서버 시작 시 저장된 혼잡 상태 불러오기
def load_zone_status():
    if STATUS_FILE.exists():
        try:
            with STATUS_FILE.open("r", encoding="utf-8") as file:
                saved = json.load(file)

            if (
                isinstance(saved, dict)
                and set(saved) == set(DEFAULT_STATUS)
                and all(
                    value in ("NORMAL", "CAUTION", "CROWDED")
                    for value in saved.values()
                )
            ):
                return saved

        except (OSError, json.JSONDecodeError):
            print("저장된 혼잡 상태를 읽을 수 없습니다.")

    return DEFAULT_STATUS.copy()


# 현재 구역별 혼잡 상태
zone_status = load_zone_status()


# 혼잡 상태를 파일에 저장
def save_zone_status():
    with STATUS_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            zone_status,
            file,
            ensure_ascii=False,
            indent=4
        )


# =====================================
# 2. ESP32 연결 및 이동 경로 설정
# =====================================

esp32_clients = set()

# 실제 설치할 통로 기준
VALID_MOVES = {
    ("A", "B"),
    ("B", "A"),
    ("B", "C"),
    ("C", "B")
}


# =====================================
# 3. 센서 기록 저장 및 조회
# =====================================

HISTORY_FILE = Path(__file__).with_name("sensor_history.jsonl")


# 정상 센서 데이터를 파일에 저장
def save_sensor_history(sensor_data):
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "device_id": sensor_data["device_id"],
        "sensor": sensor_data["sensor"],
        "from_zone": sensor_data["from_zone"],
        "to_zone": sensor_data["to_zone"],
        "direction": sensor_data["direction"]
    }

    with HISTORY_FILE.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(record, ensure_ascii=False) + "\n"
        )

    print("센서 데이터 저장 완료:", record)


# 저장된 이동 기록 조회
@app.get("/sensor-history")
def get_sensor_history():
    if not HISTORY_FILE.exists():
        return {"count": 0, "history": []}

    history = []

    with HISTORY_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                history.append(json.loads(line))

    return {
        "count": len(history),
        "history": history
    }


# =====================================
# 4. 데이터 형식 및 명령 생성
# =====================================

class ZoneStatus(BaseModel):
    zone: Literal["A", "B", "C"]
    status: Literal["NORMAL", "CAUTION", "CROWDED"]
    count: Optional[int] = None   # [시연 추가] 구역 인원 (AI가 알려주면 웹 화면에 표시)


# LCD / LED 제어 명령 생성
def make_display_command(zone, status):
    return {
        "type": "DISPLAY",
        "zone": zone,
        "status": status,
        "lcd_message": f"{zone} ZONE {status}",
        "led_pattern": status
    }


# [시연 추가] 코틀린 웹 서버와 TCP 서버 간 통신(5002) + 백업용 웹 화면(/admin, /m)
from web_bridge import WebBridge

bridge = WebBridge(
    app, zone_status, esp32_clients, save_zone_status,
    make_display_command, HISTORY_FILE
)

# [시연 추가] 웹캠으로 빨간 점 세기 → 구역별 인원 → 혼잡 판단 → ESP32/웹 화면
# 웹캠이 없거나 테스트할 때는 False로 바꾸기
USE_CAMERA = True
if USE_CAMERA:
    from vision import start_vision
    start_vision(bridge.set_counts)


# =====================================
# 5. 센서 데이터 오류 검사
# =====================================

def validate_sensor_data(data):
    if not isinstance(data, dict):
        return "Data must be a JSON object"

    required = [
        "device_id",
        "sensor",
        "from_zone",
        "to_zone",
        "direction"
    ]

    for field in required:
        if field not in data:
            return f"Missing field: {field}"

    if data["device_id"] != "ESP32_01":
        return "Unknown device"

    if data["sensor"] != "TOF":
        return "Unsupported sensor"

    start = data["from_zone"]
    end = data["to_zone"]

    if not isinstance(start, str) or not isinstance(end, str):
        return "Zone must be a string"

    if start not in zone_status or end not in zone_status:
        return "Unknown zone"

    if (start, end) not in VALID_MOVES:
        return "Invalid movement path"

    expected_direction = f"{start}_TO_{end}"

    if data["direction"] != expected_direction:
        return "Direction does not match zones"

    return None


# =====================================
# 6. FastAPI 기본 기능
# =====================================

@app.get("/")
def home():
    return {"message": "FastAPI server running"}


# 현재 전체 구역 혼잡 상태 조회
@app.get("/zone-status")
def get_zone_status():
    return zone_status


# 외부 프로그램에서 혼잡 상태 변경
@app.post("/zone-status")
async def update_zone_status(data: ZoneStatus):

    # 상태 변경
    zone_status[data.zone] = data.status

    # 변경된 상태 저장
    save_zone_status()
    bridge.note_status(data.zone, data.status, data.count)   # [시연 추가]

    # LCD / LED 명령 생성
    command = make_display_command(
        data.zone,
        data.status
    )

    # 연결된 ESP32에 즉시 전송
    for client in list(esp32_clients):
        try:
            await client.send_json(command)
        except Exception:
            esp32_clients.discard(client)

    print("혼잡 상태 변경:", command)

    return {
        "success": True,
        "zone": data.zone,
        "status": data.status,
        "connected_clients": len(esp32_clients)
    }


# =====================================
# 7. ESP32 WebSocket 양방향 통신
# =====================================

@app.websocket("/ws/esp32")
async def esp32_socket(websocket: WebSocket):

    await websocket.accept()

    esp32_clients.add(websocket)

    print("ESP32 connected")

    try:
        # 최초 접속 시 전체 구역 상태 전송
        await websocket.send_json({
            "type": "INITIAL_STATUS",
            "zones": zone_status.copy()
        })

        while True:
            # ESP32에서 데이터 수신
            message = await websocket.receive_text()

            try:
                sensor_data = json.loads(message)

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "ERROR",
                    "message": "Invalid JSON"
                })
                continue

            # 센서 데이터 검사
            error = validate_sensor_data(sensor_data)

            if error is not None:
                await websocket.send_json({
                    "type": "ERROR",
                    "message": error
                })
                continue

            start = sensor_data["from_zone"]
            end = sensor_data["to_zone"]

            print(f"이동 감지: {start} -> {end}")

            # 정상 센서 데이터 기록
            save_sensor_history(sensor_data)
            bridge.on_pass(sensor_data)   # [시연 추가] 이동 기록 (30초 유입 계산, 화면 표시)

            # 현재 혼잡 상태 확인 후 명령 전송
            await websocket.send_json(
                make_display_command(
                    end,
                    zone_status[end]
                )
            )

    except WebSocketDisconnect:
        print("ESP32 disconnected")

    finally:
        esp32_clients.discard(websocket)


# [시연 추가] python main.py 로도 실행 가능
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
