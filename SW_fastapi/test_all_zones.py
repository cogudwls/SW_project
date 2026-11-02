
import json
import time
import urllib.request

SERVER_URL = "http://127.0.0.1:5000/zone-status"

# 구역별 테스트 상태
test_data = [
    {"zone": "A", "status": "NORMAL"},
    {"zone": "B", "status": "CAUTION"},
    {"zone": "C", "status": "CROWDED"},
    {"zone": "A", "status": "CROWDED"},
    {"zone": "B", "status": "NORMAL"},
    {"zone": "C", "status": "NORMAL"}
]

for data in test_data:
    body = json.dumps(data).encode("utf-8")

    request = urllib.request.Request(
        SERVER_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))

        print(f"{data['zone']}구역 변경 성공: {result['status']}")

    except Exception as e:
        print("전송 오류:", e)

    time.sleep(2)

print("전체 구역 테스트 완료!")
