
import network
import asyncio
import json
import socket
from ws import AsyncWebsocketClient

# =====================================
# 1. Wi-Fi 및 FastAPI 서버 설정
# =====================================

SSID = "YOUR_WIFI_NAME"
PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP = "172.20.10.6"
SERVER_PORT = 5000
SERVER_URL = "ws://172.20.10.6:5000/ws/esp32"


# =====================================
# 2. Wi-Fi 연결 함수
# =====================================

async def connect_wifi():
    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)

    if wifi.isconnected():
        current_ssid = wifi.config("essid")

        if current_ssid == SSID:
            print("Wi-Fi 이미 연결됨")
            print("ESP32 IP:", wifi.ifconfig()[0])
            return True

        print("다른 Wi-Fi에 연결되어 재연결합니다.")
        wifi.disconnect()
        await asyncio.sleep(1)

    print("Wi-Fi 연결 시도...")

    try:
        wifi.connect(SSID, PASSWORD)

        for i in range(20):
            if wifi.isconnected():
                print("Wi-Fi 연결 성공!")
                print("ESP32 IP:", wifi.ifconfig()[0])
                return True

            await asyncio.sleep(1)

    except OSError as e:
        print("Wi-Fi 오류:", e)

    print("Wi-Fi 연결 실패")
    return False


# =====================================
# 3. FastAPI 서버 TCP 연결 확인
# =====================================

def check_server():
    sock = None

    try:
        print("FastAPI 서버 연결 확인...")

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


# =====================================
# 4. 서버 메시지 처리
# =====================================

def handle_command(command):

    message_type = command.get("type")

    if message_type == "INITIAL_STATUS":
        print("===== 전체 구역 상태 =====")

        zones = command.get("zones", {})

        for zone, status in zones.items():
            print(zone, "구역:", status)

        print("========================")

    elif message_type == "DISPLAY":
        print("===== 혼잡 상태 변경 =====")
        print("구역:", command.get("zone"))
        print("상태:", command.get("status"))
        print("LCD:", command.get("lcd_message"))
        print("LED:", command.get("led_pattern"))
        print("========================")

    elif message_type == "ERROR":
        print("서버 오류:", command.get("message"))

    else:
        print("기타 서버 메시지:", command)


# =====================================
# 5. WebSocket 실시간 통신
# =====================================

async def main():

    while True:
        client = None

        try:
            # Wi-Fi 연결 확인
            if not await connect_wifi():
                print("3초 후 Wi-Fi 재연결...")
                await asyncio.sleep(3)
                continue

            # FastAPI 서버 연결 확인
            if not check_server():
                print("3초 후 서버 연결 재시도...")
                await asyncio.sleep(3)
                continue

            # WebSocket 연결
            print("FastAPI WebSocket 연결 시도...")

            client = AsyncWebsocketClient()
            await client.handshake(SERVER_URL)

            print("WebSocket 연결 성공!")

            # 테스트용 가상 ToF 센서 데이터
            sensor_data = {
                "device_id": "ESP32_01",
                "sensor": "TOF",
                "from_zone": "A",
                "to_zone": "B",
                "direction": "A_TO_B"
            }

            await client.send(json.dumps(sensor_data))
            print("테스트 센서 데이터 전송 완료!")

            print("서버 명령 대기 중...")

            # 연결 유지 및 실시간 메시지 수신
            while True:
                response = await client.recv()

                if response is None:
                    print("서버 연결 종료 감지")
                    break

                try:
                    command = json.loads(response)

                    if not isinstance(command, dict):
                        print("올바르지 않은 서버 메시지")
                        continue

                    handle_command(command)

                except (ValueError, TypeError) as e:
                    print("메시지 처리 오류:", e)

        except Exception as e:
            print("WebSocket 통신 오류:", repr(e))

        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

        print("3초 후 재연결 시도...")
        await asyncio.sleep(3)


# =====================================
# 6. 프로그램 실행
# =====================================

try:
    asyncio.run(main())

except KeyboardInterrupt:
    print("ESP32 프로그램 종료")
