import cv2

# 외장 웹캠 번호: 화면이 안 나오거나 내장 카메라면 변경
CAMERA_INDEX = 1

cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

try:
    if not cap.isOpened():
        print("카메라를 열 수 없습니다. 카메라 번호를 바꿔주세요.")
    else:
        print("카메라 연결 성공! 종료하려면 화면에서 Q를 누르세요.")

        while True:
            success, frame = cap.read()

            if not success:
                print("카메라 영상을 읽을 수 없습니다.")
                break

            cv2.imshow("Camera Test", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
finally:
    cap.release()
    cv2.destroyAllWindows()