# SW_fastapi - FastAPI 서버 (ESP32 연결 + 혼잡 판단 + 서버 간 통신)

## 구조
```
[ESP32] ─WebSocket /ws/esp32─ [FastAPI 서버 :5000] ═TCP :5002═ [코틀린 웹 서버 crowd-server :5001] ─ 브라우저
                                      │
                               웹캠 빨간 점 세기
```

## 파일
| 파일                     | 담당 | 하는 일 |
|------------------------| --- | --- |
| `main.py`              | 수경 (+시연 연결부) | FastAPI 서버 본체. ESP32 WebSocket, 혼잡 상태 저장, LCD/LED 명령 생성 |
| `server_link_tcp.py`   | 시연 | 서버 간 TCP 통신 모듈. 스레드 기반 full-duplex, JSON 한 줄 규격, ack·재전송(2초, 최대 3회)·하트비트(5초)·재접속 |
| `dashboard_bridge.py`  | 시연 | 코틀린 서버로 상태 전송(1초), 관리자 제어 수신 → ESP32 명령, 혼잡 판단 규칙, 백업용 웹 주소 |
| `red_dot_counter.py`   | 시연 | 웹캠으로 빨간 점(관객 말) 검출 → 구역 A/B/C별 인원 (OpenCV) |
| `static/manager.html`  | 시연 | 관리자 화면 (PC) |
| `static/audience.html` | 시연 | 관객 화면 (모바일) |
| `test_*.py`            | 수경, 시연 | 테스트용 (가짜 AI, 가짜 ESP32 등) |

## 전체 프로젝트 구성
| 폴더 | 담당 | 내용 |
| --- | --- | --- |
| `ESP32/` | 수경 | ESP32 MicroPython: ToF 이동 감지, LCD/LED 출력, FastAPI와 WebSocket 통신 |
| `SW_fastapi/` | 수경, 시연 | FastAPI 서버 (이 폴더, 아래 표 참고) |
| `crowd-server/` | 형진 (+시연 통신·화면) | 코틀린 Spring Boot 웹 서버 (:5001). FastAPI와 TCP 연결, 관리자·관객 화면 제공 |

## 실행
```
python -m pip install "fastapi[standard]" opencv-python
python main.py
```
- 웹캠 없이 테스트: `main.py`의 `USE_CAMERA = False`
- ESP32 없이 테스트: `python test_fake_esp32.py`
- 코틀린 서버(crowd-server)를 켜면 5002로 자동 접속 → `http://localhost:5001/manager`
