
import asyncio
import json
import websockets


async def main():
    uri = "ws://127.0.0.1:5000/ws/esp32"

    async with websockets.connect(uri) as ws:
        initial = await ws.recv()
        print("초기 상태:", initial)

        tests = [
            # 정상 이동
            {
                "device_id": "ESP32_01",
                "sensor": "TOF",
                "from_zone": "A",
                "to_zone": "B",
                "direction": "A_TO_B"
            },

            # 존재하지 않는 구역
            {
                "device_id": "ESP32_01",
                "sensor": "TOF",
                "from_zone": "A",
                "to_zone": "Z",
                "direction": "A_TO_Z"
            },

            # 같은 구역으로 이동
            {
                "device_id": "ESP32_01",
                "sensor": "TOF",
                "from_zone": "A",
                "to_zone": "A",
                "direction": "A_TO_A"
            },

            # 방향 정보 누락
            {
                "device_id": "ESP32_01",
                "sensor": "TOF",
                "from_zone": "A",
                "to_zone": "B"
            }
        ]

        for i, data in enumerate(tests, start=1):
            await ws.send(json.dumps(data))
            response = await ws.recv()

            print(f"\n테스트 {i}")
            print("서버 응답:", response)

        # 잘못된 JSON 문자열
        await ws.send("{invalid json")
        response = await ws.recv()

        print("\n테스트 5")
        print("서버 응답:", response)

        # 오류가 발생한 뒤에도 정상 동작하는지 확인
        await ws.send(json.dumps(tests[0]))
        response = await ws.recv()

        print("\n테스트 6 - 오류 발생 후 정상 동작")
        print("서버 응답:", response)


asyncio.run(main())
