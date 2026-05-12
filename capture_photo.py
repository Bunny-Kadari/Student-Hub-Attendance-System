import cv2

cam = cv2.VideoCapture(0)

print("Press SPACE to capture photo")
print("Press Q to quit")

while True:
    ret, frame = cam.read()
    cv2.imshow("Capture Photo", frame)

    key = cv2.waitKey(1)

    if key % 256 == 32:  # SPACE pressed
        cv2.imwrite("camera.jpg", frame)
        print("Photo captured as camera.jpg")
        break

    elif key % 256 == ord('q'):
        break

cam.release()
cv2.destroyAllWindows()

