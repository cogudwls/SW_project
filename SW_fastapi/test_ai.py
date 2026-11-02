
import json
import time
import urllib.request

# FastAPI 서버 주소
SERVER_URL = "http://127.0.0.1:5000/zone-status"

# 가상의 영상 분석 결과
test_data = [
    {"zone": "B", "status": "NORMAL"},
    {"zone": "B", "status": "CAUTION"},
    {"zone": "B", "status": "CROWDED"}
]

for data in test_data:
    print(f"\n영상 분석 결과 전송: {data}")

    # Python 데이터를 JSON으로 변환
    body = json.dumps(data).encode("utf-8")

    # FastAPI 서버에 HTTP POST 요청
    request = urllib.request.Request(
        SERVER_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request, timeout=5
        ) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

            print("서버 응답:", result)

    except Exception as e:
        print("서버 통신 오류:", e)

    # 다음 혼잡 상태를 보내기 전 3초 대기
    time.sleep(3)

print("\n가상 영상 분석 테스트 완료!")
