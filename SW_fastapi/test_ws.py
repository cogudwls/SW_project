
import asyncio
import json
import websockets

# FastAPI 서버 주소
SERVER_URL = "ws://127.0.0.1:5000/ws/esp32"


async def connect_esp32():

    while True:
        try:
            print("FastAPI 서버 연결 시도 중...")

            async with websockets.connect(
                SERVER_URL
            ) as websocket:

                print("가상 ESP32 연결 성공!")

                # 가상 ToF 센서 데이터
                sensor_data = {
                    "device_id": "ESP32_01",
                    "sensor": "TOF",
                    "from_zone": "A",
                    "to_zone": "B",
                    "direction": "A_TO_B"
                }

                # 서버에 센서 데이터 전송
                await websocket.send(
                    json.dumps(sensor_data)
                )

                print("센서 데이터 전송 완료")

                # 서버로부터 명령 계속 수신
                while True:
                    response = await websocket.recv()

                    command = json.loads(response)

                    print("서버에서 받은 명령:", command)

        except (
            OSError,
            websockets.exceptions.ConnectionClosed
        ) as e:
            print("서버 연결 끊김:", e)
            print("3초 후 다시 연결합니다.")

        # 연결 실패 또는 끊김 시 3초 대기
        await asyncio.sleep(3)


try:
    asyncio.run(connect_esp32())

except KeyboardInterrupt:
    print("가상 ESP32 테스트 종료")
