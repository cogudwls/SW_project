[전체 구조]
[ESP32] ─WebSocket─ [FastAPI 서버: 수경, 5000] ═TCP 5002═ [코틀린 웹 서버: 형진, 5001] ─ 브라우저
                         │                     (시연: 서버 간 통신)
                    웹캠 빨간 점 세기 (vision.py)

==================================================
1_sukyung_fastapi  →  수경이 FastAPI 폴더에 넣기
==================================================
- main.py      : 수경이 main.py에 [시연 추가] 표시된 부분만 추가한 버전
- web_bridge.py, tcp_link.py, vision.py : main.py 옆에
- static/      : 백업용 화면 (FastAPI에서 바로 /admin, /m 으로도 볼 수 있음)
- fake_esp32.py: ESP32 없이 테스트할 때

설치: python -m pip install "fastapi[standard]" opencv-python
실행: python main.py
웹캠이 아직 없으면 main.py의 USE_CAMERA = False 로 바꾸기
ESP32가 새로 받아야 하는 명령: DISPLAY의 status "CLOSED", type "MESSAGE"

==================================================
2_hyeongjin_kotlin  →  형진이 Spring Boot 프로젝트에 넣기
==================================================
- FastApiLink.kt, WebController.kt : src/main/kotlin/(패키지 폴더)/ 에 넣고
                                      맨 위 package 줄을 프로젝트 패키지로 바꾸기
- static/admin.html, mobile.html   : src/main/resources/static/ 에 넣기
- application.properties.txt 내용   : src/main/resources/application.properties 에 추가
- 필요한 의존성: spring-boot-starter-web (Jackson 포함), Spring Boot 3 (jakarta)
  Spring Boot 2라면 jakarta.annotation.PostConstruct → javax.annotation.PostConstruct

==================================================
실행 순서
==================================================
1. 수경: python main.py           (FastAPI 5000 + 서버 간 통신 5002 대기)
2. 형진: 코틀린 서버 실행            (5001, FastAPI 5002에 자동 접속, 끊기면 3초마다 재접속)
3. 브라우저: http://localhost:5001/admin   (관리자)
            http://localhost:5001/m       (관객, 폰은 노트북IP:5001/m)
