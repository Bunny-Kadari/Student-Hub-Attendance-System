import cv2
import face_recognition

# Load image
image = face_recognition.load_image_file("test.jpg")

# Find faces
face_locations = face_recognition.face_locations(image)

print("Number of faces found:", len(face_locations))

# Draw rectangle
image_cv = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

for (top, right, bottom, left) in face_locations:
    cv2.rectangle(image_cv, (left, top), (right, bottom), (0, 255, 0), 2)

cv2.imshow("Faces", image_cv)
cv2.waitKey(0)
cv2.destroyAllWindows()

