# red_dot_counter.py
# 빨간 자석 집계 + 수동 렌즈 보정 + 카메라 미리보기

import json
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np


CAMERA_INDEX = 1

ZONES = ["A", "B", "C"]
ZONE_EDGES = [0.0, 1 / 3, 2 / 3, 1.0]

# 화면 가장자리 2%만 제외
ROI_LEFT, ROI_RIGHT = 0.02, 0.98
ROI_TOP, ROI_BOTTOM = 0.02, 0.98

MIN_AREA = 80

# 0.5초마다 집계하고 3회 연속 같으면 서버에 반영
INTERVAL = 0.5
CONFIRM_FRAMES = 3

WINDOW_NAME = "Camera Preview"
LENS_FILE = Path(__file__).with_name("lens_settings.json")

_vision_thread = None


class LensCorrection:
    def __init__(self):
        self.cache_key = None
        self.maps = None
        self.strength = 100
        self.zoom = 100

        try:
            saved = json.loads(
                LENS_FILE.read_text(encoding="utf-8")
            )
            self.strength = max(
                0, min(200, int(saved["strength"]))
            )
            self.zoom = max(
                50, min(160, int(saved["zoom"]))
            )
        except (OSError, ValueError, KeyError, TypeError):
            pass

        print("[보정] J/L: 왜곡 감소/증가")
        print("[보정] U/I: 축소/확대")
        print("[보정] R: 초기화, S: 저장, Q: 카메라 종료")
        self.print_values()

    def print_values(self):
        print(
            f"[보정값] Strength={self.strength}, "
            f"Zoom={self.zoom}%"
        )

    def handle_key(self, key):
        if key == ord("j"):
            self.strength = max(0, self.strength - 2)
        elif key == ord("l"):
            self.strength = min(200, self.strength + 2)
        elif key == ord("u"):
            self.zoom = max(50, self.zoom - 2)
        elif key == ord("i"):
            self.zoom = min(160, self.zoom + 2)
        elif key == ord("r"):
            self.strength = 100
            self.zoom = 100
        else:
            return False

        self.print_values()
        return True

    def apply(self, frame):
        h, w = frame.shape[:2]
        key = (w, h, self.strength, self.zoom)

        if key != self.cache_key:
            # 실제 캘리브레이션 대신 사용하는 수동 근사값
            k1 = (self.strength - 100) / 200.0
            focal = float(max(w, h))

            camera_matrix = np.array([
                [focal, 0, (w - 1) / 2],
                [0, focal, (h - 1) / 2],
                [0, 0, 1],
            ], dtype=np.float64)

            distortion = np.array(
                [k1, 0, 0, 0, 0],
                dtype=np.float64,
            )

            output_matrix = camera_matrix.copy()
            output_matrix[0, 0] *= self.zoom / 100.0
            output_matrix[1, 1] *= self.zoom / 100.0

            self.maps = cv2.initUndistortRectifyMap(
                camera_matrix,
                distortion,
                None,
                output_matrix,
                (w, h),
                cv2.CV_32FC1,
            )
            self.cache_key = key

        return cv2.remap(
            frame,
            self.maps[0],
            self.maps[1],
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )

    def save(self):
        try:
            LENS_FILE.write_text(
                json.dumps(
                    {
                        "strength": self.strength,
                        "zoom": self.zoom,
                    },
                    indent=4,
                ),
                encoding="utf-8",
            )
            print("[보정 설정 저장 완료]")
        except OSError as e:
            print("[보정 설정 저장 실패]", e)


def open_camera():
    # Windows에서는 DirectShow 사용
    if sys.platform == "win32":
        return cv2.VideoCapture(
            CAMERA_INDEX, cv2.CAP_DSHOW
        )
    return cv2.VideoCapture(CAMERA_INDEX)


def find_red_dots(frame):
    """빨간 영역 내부의 중심점을 찾아 맞닿은 자석 분리."""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mask = (
            cv2.inRange(
                hsv, (0, 80, 35), (15, 255, 255)
            )
            | cv2.inRange(
        hsv, (165, 80, 35), (180, 255, 255)
    )
    )

    # 자석 사이를 과하게 연결하지 않도록 작은 커널 사용
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, kernel
    )

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    dots = []

    for contour in contours:
        if cv2.contourArea(contour) < MIN_AREA:
            continue

        x, y, bw, bh = cv2.boundingRect(contour)

        # 덩어리 주변에 검은 여백 추가
        component = np.zeros(
            (bh + 4, bw + 4), dtype=np.uint8
        )
        local_contour = contour - np.array(
            [[[x - 2, y - 2]]], dtype=contour.dtype
        )
        cv2.drawContours(
            component,
            [local_contour],
            -1,
            255,
            cv2.FILLED,
        )

        # 경계에서 멀수록 값이 커짐: 자석 중심에서 최대
        distance = cv2.distanceTransform(
            component, cv2.DIST_L2, 5
        )
        distance = cv2.GaussianBlur(
            distance, (3, 3), 0
        )

        max_radius = float(distance.max())
        if max_radius <= 0:
            continue

        # 가까운 중심 후보를 모아서 중복 검출 방지
        size = max(3, int(max_radius * 0.9))
        if size % 2 == 0:
            size += 1

        local_max = cv2.dilate(
            distance,
            np.ones((size, size), dtype=np.uint8),
        )

        peaks = (
                (distance >= local_max - 0.001)
                & (distance >= max(2.0, max_radius * 0.55))
        ).astype(np.uint8)

        number, labels = cv2.connectedComponents(peaks)

        candidates = []

        for label in range(1, number):
            ys, xs = np.where(labels == label)

            if len(xs) == 0:
                continue

            values = distance[ys, xs]
            radius = float(values.max())

            # 최대값 영역의 중앙을 중심 후보로 선택
            best = values >= radius - 0.001
            px = float(xs[best].mean())
            py = float(ys[best].mean())

            candidates.append((px, py, radius))

        # 반지름이 큰 후보부터 선택
        candidates.sort(
            key=lambda item: item[2], reverse=True
        )
        selected = []

        for px, py, radius in candidates:
            duplicate = any(
                np.hypot(px - sx, py - sy)
                < 1.3 * max(radius, sr)
                for sx, sy, sr in selected
            )

            if not duplicate:
                selected.append((px, py, radius))

        for px, py, _ in selected:
            cx = x + px - 2
            cy = y + py - 2

            if (
                    ROI_LEFT * w <= cx <= ROI_RIGHT * w
                    and ROI_TOP * h <= cy <= ROI_BOTTOM * h
            ):
                dots.append((cx, cy))

    return dots


def count_by_zone(dots, width):
    counts = {z: 0 for z in ZONES}

    for x, _ in dots:
        ratio = x / width

        for i, zone in enumerate(ZONES):
            if ZONE_EDGES[i] <= ratio < ZONE_EDGES[i + 1]:
                counts[zone] += 1
                break

    return counts


def draw(frame, dots, counts):
    h, w = frame.shape[:2]

    # 집계 범위 표시
    cv2.rectangle(
        frame,
        (int(ROI_LEFT * w), int(ROI_TOP * h)),
        (int(ROI_RIGHT * w), int(ROI_BOTTOM * h)),
        (150, 150, 150),
        1,
    )

    for edge in ZONE_EDGES[1:-1]:
        x = int(edge * w)
        cv2.line(
            frame, (x, 0), (x, h),
            (255, 255, 255), 1
        )

    for x, y in dots:
        cv2.circle(
            frame,
            (int(x), int(y)),
            14,
            (0, 255, 0),
            2,
        )

    for i, zone in enumerate(ZONES):
        cv2.putText(
            frame,
            f"{zone}: {counts[zone]}",
            (int(ZONE_EDGES[i] * w) + 10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )

    return frame


def _loop(callback):
    cap = open_camera()
    lens = LensCorrection()

    candidate = None
    same_count = 0
    confirmed = None
    last_sample = None

    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                print("[카메라] 재연결을 시도합니다.")
                candidate = None
                same_count = 0
                last_sample = None

                cap.release()
                time.sleep(2)
                cap = open_camera()
                continue

            # 보정한 영상으로 탐지와 미리보기 모두 처리
            frame = lens.apply(frame)
            dots = find_red_dots(frame)
            counts = count_by_zone(dots, frame.shape[1])

            now = time.monotonic()

            # 영상은 계속 읽고 서버 집계만 0.5초 간격으로 확인
            if last_sample is None or now - last_sample >= INTERVAL:
                last_sample = now

                if counts == candidate:
                    same_count += 1
                else:
                    candidate = counts.copy()
                    same_count = 1

                if (
                        same_count >= CONFIRM_FRAMES
                        and counts != confirmed
                ):
                    callback(counts.copy())
                    confirmed = counts.copy()
                    print("[인원 반영]", confirmed)

            preview = draw(frame.copy(), dots, counts)
            h = preview.shape[0]

            if confirmed is None:
                label = "Confirmed: waiting"
            else:
                label = "Confirmed: " + "  ".join(
                    f"{z}:{confirmed[z]}" for z in ZONES
                )

            cv2.putText(
                preview,
                label,
                (10, h - 48),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255),
                2,
            )

            cv2.putText(
                preview,
                (
                    f"Lens:{lens.strength} Zoom:{lens.zoom}% "
                    "J/L lens U/I zoom S save R reset Q quit"
                ),
                (10, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (0, 255, 255),
                1,
            )

            cv2.imshow(WINDOW_NAME, preview)

            key = cv2.waitKey(1) & 0xFF

            # 대문자 입력도 처리
            if ord("A") <= key <= ord("Z"):
                key += 32

            if lens.handle_key(key):
                candidate = None
                same_count = 0
                last_sample = None

            elif key == ord("s"):
                lens.save()

            elif key == ord("q"):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[카메라] 집계 종료. 웹에는 마지막 값이 남습니다.")


def start_vision(callback):
    """main.py에서 카메라 집계 시작."""
    global _vision_thread

    if _vision_thread is not None and _vision_thread.is_alive():
        return

    _vision_thread = threading.Thread(
        target=_loop,
        args=(callback,),
        daemon=True,
    )
    _vision_thread.start()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 사진 테스트에도 저장된 보정값 적용
        image = cv2.imread(sys.argv[1])

        if image is None:
            raise SystemExit("사진을 읽을 수 없습니다.")

        lens = LensCorrection()
        image = lens.apply(image)
        dots = find_red_dots(image)
        counts = count_by_zone(dots, image.shape[1])

        output = Path(__file__).with_name("vision_result.jpg")
        success = cv2.imwrite(
            str(output),
            draw(image, dots, counts),
        )

        print("인식 결과:", counts)
        print("결과 저장:", output if success else "실패")
    else:
        _loop(lambda counts: None)