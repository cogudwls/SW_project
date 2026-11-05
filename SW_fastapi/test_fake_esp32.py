# test_fake_esp32.py - 가짜 ESP32 (실물 없이 테스트할 때) (수경이 서버 /ws/esp32 형식 그대로)
# 실행: python test_fake_esp32.py
# 3초마다 G1(A-B), G2(B-C) 중 한 곳을 지나가는 이동을 보내고, 서버가 보내는 명령을 출력한다.
import asyncio
import json
import random

import websockets

URL = "ws://127.0.0.1:5000/ws/esp32"
MOVES = [("A", "B"), ("B", "A"), ("B", "C"), ("C", "B")]


async def listen(ws):
    async for msg in ws:
        print("[ESP32] 서버 명령:", json.loads(msg))


async def main():
    while True:
        try:
            async with websockets.connect(URL) as ws:
                print("[ESP32] 서버 연결됨")
                asyncio.create_task(listen(ws))
                while True:
                    a, b = random.choice(MOVES)
                    await ws.send(json.dumps({"device_id": "ESP32_01", "sensor": "TOF",
                                              "from_zone": a, "to_zone": b, "direction": f"{a}_TO_{b}"}))
                    print(f"[ESP32] 이동 감지 보냄: {a} -> {b}")
                    await asyncio.sleep(3)
        except (OSError, websockets.exceptions.ConnectionClosed):
            print("[ESP32] 연결 끊김 → 3초 뒤 재연결")
            await asyncio.sleep(3)

asyncio.run(main())
