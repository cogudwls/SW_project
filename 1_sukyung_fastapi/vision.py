# vision.py - 웹캠으로 빨간 점(관객 말)을 세서 구역별 인원을 구한다 (YOLO 대신 OpenCV 색 검출)
#
# 수경이 main.py에서 한 줄로 시작:
#   start_vision(bridge.set_counts)
#
# 혼자 테스트:  python vision.py          → 웹캠 화면에 인식 결과를 그려서 보여줌 (q 누르면 종료)
#              python vision.py 사진.jpg  → 사진 한 장으로 테스트
#
# 조정할 값 (모형을 찍어 보고 맞추기)
#   ZONE_EDGES : 화면 가로를 A/B/C로 나누는 비율. 기본은 3등분
#   ROI_TOP/BOTTOM : 세로로 볼 범위 (무대·LED가 찍히는 부분은 빼기)
#   MIN_AREA  : 이보다 작은 빨간 덩어리는 잡음으로 보고 무시
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

CAMERA_INDEX = 0                 # 웹캠 번호 (노트북 내장 카메라가 0이면 USB 웹캠은 1일 수 있음)
ZONE_EDGES = [0.0, 1 / 3, 2 / 3, 1.0]
ZONES = ["A", "B", "C"]
ROI_TOP, ROI_BOTTOM = 0.15, 0.95  # 위 15%(무대)와 아래 5%는 안 봄
MIN_AREA = 80                    # 픽셀 넓이
INTERVAL = 0.5                   # 0.5초마다 1장 (초당 2장)


def find_red_dots(frame):
    """프레임에서 빨간 점들의 중심 좌표 리스트를 돌려줌"""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # 빨간색은 HSV 색상환 양 끝(0 근처, 180 근처)에 걸쳐 있어서 두 범위를 합친다
    mask = cv2.inRange(hsv, (0, 120, 80), (10, 255, 255)) | cv2.inRange(hsv, (170, 120, 80), (180, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))   # 작은 잡음 제거
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    dots = []
    for c in contours:
        if cv2.contourArea(c) < MIN_AREA:
            continue
        m = cv2.moments(c)
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        if ROI_TOP * h <= cy <= ROI_BOTTOM * h:
            dots.append((cx, cy))
    return dots


def count_by_zone(dots, width):
    counts = {z: 0 for z in ZONES}
    for x, _ in dots:
        r = x / width
        for i, z in enumerate(ZONES):
            if ZONE_EDGES[i] <= r < ZONE_EDGES[i + 1]:
                counts[z] += 1
    return counts


def draw(frame, dots, counts):
    h, w = frame.shape[:2]
    for e in ZONE_EDGES[1:-1]:
        cv2.line(frame, (int(e * w), 0), (int(e * w), h), (255, 255, 255), 1)
    for x, y in dots:
        cv2.circle(frame, (int(x), int(y)), 14, (0, 255, 0), 2)
    for i, z in enumerate(ZONES):
        cv2.putText(frame, f"{z}: {counts[z]}", (int(ZONE_EDGES[i] * w) + 10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    return frame


def _loop(callback):
    cap = cv2.VideoCapture(CAMERA_INDEX)
    recent = deque(maxlen=3)                               # 최근 3장 중간값으로 흔들림 줄이기
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[vision] 웹캠을 읽을 수 없어요. CAMERA_INDEX를 확인하세요")
            time.sleep(2)
            cap = cv2.VideoCapture(CAMERA_INDEX)
            continue
        recent.append(count_by_zone(find_red_dots(frame), frame.shape[1]))
        smooth = {z: int(np.median([r[z] for r in recent])) for z in ZONES}
        callback(smooth)
        time.sleep(INTERVAL)


def start_vision(callback):
    """다른 스레드에서 웹캠을 계속 읽고, 0.5초마다 callback({"A":2,"B":3,"C":1}) 호출"""
    threading.Thread(target=_loop, args=(callback,), daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) > 1:                                  # 사진으로 테스트
        img = cv2.imread(sys.argv[1])
        dots = find_red_dots(img)
        counts = count_by_zone(dots, img.shape[1])
        print("인식 결과:", counts)
        cv2.imwrite("vision_result.jpg", draw(img, dots, counts))
        print("vision_result.jpg 에 결과 그림 저장")
    else:                                                  # 웹캠으로 테스트
        cap = cv2.VideoCapture(CAMERA_INDEX)
        while True:
            ok, frame = cap.read()
            if not ok:
                print("웹캠을 읽을 수 없어요")
                break
            dots = find_red_dots(frame)
            counts = count_by_zone(dots, frame.shape[1])
            cv2.imshow("vision (q = quit)", draw(frame, dots, counts))
            if cv2.waitKey(30) & 0xFF == ord("q"):
                break
