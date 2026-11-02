
import json
import urllib.request

SERVER_URL = "http://127.0.0.1:5000/zone-status"

try:
    with urllib.request.urlopen(
        SERVER_URL, timeout=5
    ) as response:

        data = json.loads(
            response.read().decode("utf-8")
        )

    print("=== 현재 구역별 혼잡 상태 ===")

    for zone, status in data.items():
        print(f"{zone}구역: {status}")

except Exception as e:
    print("서버 연결 오류:", e)
