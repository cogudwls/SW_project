
import network
import asyncio
import json
import socket
from ws import AsyncWebsocketClient

# Wi-Fi 설정
SSID = "YOUR_WIFI_NAME"
PASSWORD = "YOUR_WIFI_PASSWORD"
# FastAPI 서버 설정
SERVER_IP = "172.20.10.6"
SERVER_PORT = 5000
SERVER_URL = "ws://172.20.10.6:5000/ws/esp32"


async def connect_wifi():
    wifi = network.WLAN(network.STA_IF)

    try:
        # 기존 Wi-Fi 연결 상태 확인
        if wifi.isconnected():
            current_ssid = wifi.config("essid")

            if current_ssid == SSID:
                print("Wi-Fi 이미 연결됨")
                print("ESP32 IP:", wifi.ifconfig()[0])
                return True

        # 연결 상태 초기화
        print("Wi-Fi 초기화 중...")
        wifi.active(False)
        await asyncio.sleep(2)

        wifi.active(True)
        await asyncio.sleep(1)

        print("Wi-Fi 연결 시도...")
        wifi.connect(SSID, PASSWORD)

        for i in range(20):
            if wifi.isconnected():
                print("Wi-Fi 연결 성공!")
                print("ESP32 IP:", wifi.ifconfig()[0])
                return True

            await asyncio.sleep(1)

        print("Wi-Fi 연결 실패")
        print("상태 코드:", wifi.status())
        return False

    except OSError as e:
        print("Wi-Fi 내부 오류:", e)
        return False



def test_server_connection():
    sock = None

    try:
        print("FastAPI TCP 연결 확인...")

        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((SERVER_IP, SERVER_PORT))

        print("TCP 연결 성공!")
        return True

    except OSError as e:
        print("TCP 연결 실패:", e)
        return False

    finally:
        if sock is not None:
            sock.close()


async def main():
    client = None

    # 1. Wi-Fi 연결
    if not await connect_wifi():
        return

    # 2. 서버 접근 확인
    if not test_server_connection():
        print("노트북 IP, FastAPI 실행 상태를 확인하세요.")
        return

    try:
        # 3. WebSocket 연결
        print("FastAPI WebSocket 연결 시도...")

        client = AsyncWebsocketClient()
        await client.handshake(SERVER_URL)

        print("WebSocket 연결 성공!")

        # 4. 테스트 센서 데이터 전송
        sensor_data = {
            "device_id": "ESP32_01",
            "sensor": "TOF",
            "from_zone": "A",
            "to_zone": "B",
            "direction": "A_TO_B"
        }

        await client.send(json.dumps(sensor_data))
        print("센서 데이터 전송 완료!")

        # 5. 서버 응답 확인
        for i in range(2):
            response = await asyncio.wait_for(
                client.recv(),
                timeout=10
            )

            if response is None:
                print("서버 연결 종료")
                break

            print("서버 응답:", response)

        print("WebSocket 테스트 완료!")

    except Exception as e:
        print("WebSocket 통신 오류:", repr(e))

    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


asyncio.run(main())
