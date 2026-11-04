# server_link_tcp.py - 시연 담당: 서버 간 TCP 통신 모듈 (스레드 기반 full-duplex, ack·재전송·하트비트·재접속)
#
#  [코틀린 웹 서버(FastApiLink.kt) 또는 Python 클라이언트]  --LinkClient-->  TCP 5002  <--LinkServer--  [FastAPI 서버]
#
# 응용계층 프로토콜 (직접 설계)
#  - 메시지 = JSON 한 줄 + 줄바꿈(\n). 줄바꿈이 메시지 경계.
#  - 중요한 메시지(예: pass)는 reliable=True로 보내면 "id"가 붙고,
#    받는 쪽이 {"type":"ack","id":..}로 답한다. 2초 안에 ack가 없으면 최대 3번 재전송.
#  - 같은 id가 두 번 오면(재전송 때문에) 한 번만 처리한다(중복 제거).
#  - 5초마다 heartbeat, 15초 동안 아무것도 안 오면 연결 끊김으로 본다.
#
# 스레드 구조 (full-duplex): 연결 하나당
#  - 받기 스레드   : 소켓에서 읽어서 메시지 처리
#  - 보내기 스레드 : 큐에 쌓인 메시지를 소켓으로 전송
#  - 감시 스레드   : 재전송, 하트비트, 무응답 감지
import json
import queue
import socket
import threading
import time
from collections import deque

ACK_TIMEOUT = 2.0      # ack 기다리는 시간(초)
MAX_RETRIES = 3        # 최대 재전송 횟수
HEARTBEAT_EVERY = 5.0  # 하트비트 간격(초)
DEAD_AFTER = 15.0      # 이 시간 동안 아무것도 안 오면 끊김


class Link:
    """연결 하나(소켓 하나)를 담당. 서버/클라이언트 양쪽에서 똑같이 쓴다."""

    def __init__(self, sock, name, on_message, on_close=None):
        self.sock = sock
        self.name = name
        self.on_message = on_message          # 메시지를 받으면 호출할 함수
        self.on_close = on_close
        self.out = queue.Queue()              # 보낼 메시지 큐
        self.pending = {}                     # id -> [msg, 보낸 시각, 재전송 횟수]
        self.seen = deque(maxlen=500)         # 이미 처리한 id (중복 제거용)
        self.next_id = 1
        self.lock = threading.Lock()
        self.alive = True
        self.last_rx = time.time()
        self.last_hb = 0.0
        # 통계 (웹 화면 '통신 상태'와 성능 평가에 사용)
        self.rtt_ms = deque(maxlen=50)
        self.retries = 0
        self.dropped = 0

    def start(self):
        for target in (self._recv_loop, self._send_loop, self._watch_loop):
            threading.Thread(target=target, daemon=True).start()

    # ---------- 보내기 ----------
    def send(self, msg, reliable=False):
        if not self.alive:
            return False
        if reliable:
            with self.lock:
                msg = dict(msg, id=self.next_id)
                self.pending[self.next_id] = [msg, time.time(), 0]
                self.next_id += 1
        self.out.put(msg)
        return True

    def _send_loop(self):
        while self.alive:
            try:
                msg = self.out.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.sock.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode())
            except OSError:
                self.close("보내기 실패")

    # ---------- 받기 ----------
    def _recv_loop(self):
        buf = b""
        while self.alive:
            try:
                data = self.sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                self.close("상대가 연결을 끊음")
                return
            self.last_rx = time.time()
            buf += data
            while b"\n" in buf:                      # 줄바꿈 기준으로 메시지 자르기
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        self._handle(json.loads(line))
                    except json.JSONDecodeError:
                        print(f"[{self.name}] 형식이 잘못된 메시지 무시: {line[:60]!r}")

    def _handle(self, msg):
        kind = msg.get("type")
        if kind == "ack":                            # 내가 보낸 것에 대한 답장
            with self.lock:
                p = self.pending.pop(msg.get("id"), None)
            if p:
                self.rtt_ms.append(round((time.time() - p[1]) * 1000))
            return
        if kind == "heartbeat":
            self.out.put({"type": "heartbeat_ack"})
            return
        if kind == "heartbeat_ack":
            return
        if "id" in msg:                              # 상대가 ack를 원하는 메시지
            self.out.put({"type": "ack", "id": msg["id"]})
            if msg["id"] in self.seen:               # 재전송으로 또 온 것 → 무시
                return
            self.seen.append(msg["id"])
        self.on_message(msg)

    # ---------- 감시: 재전송 / 하트비트 / 무응답 ----------
    def _watch_loop(self):
        while self.alive:
            now = time.time()
            with self.lock:
                for mid, p in list(self.pending.items()):
                    msg, sent, tries = p
                    if now - sent >= ACK_TIMEOUT:
                        if tries >= MAX_RETRIES:
                            del self.pending[mid]
                            self.dropped += 1
                            print(f"[{self.name}] id {mid} 재전송 {MAX_RETRIES}번 실패 → 포기")
                        else:
                            p[1], p[2] = now, tries + 1
                            self.retries += 1
                            print(f"[{self.name}] id {mid} ack 없음 → 재전송 {tries + 1}번째")
                            self.out.put(msg)
            if now - self.last_hb >= HEARTBEAT_EVERY:
                self.last_hb = now
                self.out.put({"type": "heartbeat"})
            if now - self.last_rx >= DEAD_AFTER:
                self.close(f"{DEAD_AFTER:.0f}초 동안 응답 없음")
            time.sleep(0.2)

    def close(self, reason=""):
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass
        print(f"[{self.name}] 연결 종료: {reason}")
        if self.on_close:
            self.on_close(self)

    def stats(self):
        r = list(self.rtt_ms)
        return {"connected": self.alive,
                "last_rx_ago": round(time.time() - self.last_rx, 1),
                "rtt_last_ms": r[-1] if r else None,
                "rtt_avg_ms": round(sum(r) / len(r)) if r else None,
                "rtt_max_ms": max(r) if r else None,
                "retries": self.retries, "dropped": self.dropped,
                "pending": len(self.pending)}


class LinkServer:
    """FastAPI 서버 쪽: 포트를 열고 AI 서버의 접속을 기다린다 (끊기면 다시 기다림)."""

    def __init__(self, port, on_message, host="0.0.0.0"):
        self.host, self.port, self.on_message = host, port, on_message
        self.link = None

    def start(self):
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen()
        print(f"[LinkServer] {self.port}번 포트에서 AI 서버 접속 대기 중")
        while True:
            sock, addr = srv.accept()
            if self.link and self.link.alive:        # 새 접속이 오면 예전 연결은 정리
                self.link.close("새 접속으로 교체")
            print(f"[LinkServer] AI 서버 접속: {addr}")
            self.link = Link(sock, "LinkServer", self.on_message)
            self.link.start()

    def send(self, msg, reliable=False):
        return self.link.send(msg, reliable) if self.link else False

    def stats(self):
        return self.link.stats() if self.link else {"connected": False}


class LinkClient:
    """AI 서버 쪽: FastAPI 서버에 접속한다. 끊기면 3초 뒤 자동 재접속."""

    def __init__(self, host, port, on_message, hello=None):
        self.host, self.port, self.on_message = host, port, on_message
        self.hello = hello or {"type": "hello", "role": "ai"}
        self.link = None
        self.connected = threading.Event()

    def start(self):
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def _connect_loop(self):
        while True:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.settimeout(None)
                self.link = Link(sock, "LinkClient", self.on_message, on_close=lambda l: self.connected.clear())
                self.link.start()
                self.link.send(self.hello)
                self.connected.set()
                print(f"[LinkClient] {self.host}:{self.port} 접속 성공")
                while self.link.alive:
                    time.sleep(0.5)
            except OSError as e:
                print(f"[LinkClient] 접속 실패({e.__class__.__name__}) → 3초 뒤 재시도")
            time.sleep(3)

    def send(self, msg, reliable=False):
        return self.link.send(msg, reliable) if self.link else False

    def stats(self):
        return self.link.stats() if self.link else {"connected": False}
