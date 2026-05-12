import cv2
import face_recognition
import os
import sqlite3
import sys
from datetime import datetime

# ---------------- GET CLASS ID FROM FLASK ----------------

if len(sys.argv) > 1:
    class_id = int(sys.argv[1])
else:
    class_id = 1

print("Running AI for class_id:", class_id)

# ---------------- LOAD KNOWN FACES ----------------

known_encodings = []
known_names = []

# 🔥 FIXED PATH
known_faces_dir = os.path.join(os.getcwd(), "static", "known_faces")

print("Loading faces from:", known_faces_dir)

for file in os.listdir(known_faces_dir):
    if file.endswith(".jpg"):

        image_path = os.path.join(known_faces_dir, file)
        image = face_recognition.load_image_file(image_path)
        encodings = face_recognition.face_encodings(image)

        if len(encodings) > 0:
            known_encodings.append(encodings[0])
            known_names.append(os.path.splitext(file)[0])

print("Known Faces Loaded:", known_names)

# ---------------- START CAMERA ----------------

video_capture = cv2.VideoCapture(0)

marked_today = set()

while True:
    ret, frame = video_capture.read()
    if not ret:
        break

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    face_locations = face_recognition.face_locations(rgb_frame)
    face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

    for face_encoding in face_encodings:

        matches = face_recognition.compare_faces(known_encodings, face_encoding)
        name = "Unknown"

        if True in matches:
            match_index = matches.index(True)
            name = known_names[match_index]

            if name not in marked_today:

                print("Recognized:", name)

                conn = sqlite3.connect("hub.db")
                cursor = conn.cursor()

                cursor.execute("SELECT id FROM students WHERE hub_id = ?", (name,))
                student = cursor.fetchone()

                if student:
                    student_id = student[0]
                    today = datetime.now().strftime("%Y-%m-%d")

                    cursor.execute("""
                        INSERT INTO attendance (student_id, class_id, date, status)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(student_id, class_id, date)
                        DO UPDATE SET status='Present'
                    """, (student_id, class_id, today, "Present"))

                    conn.commit()
                    print("Attendance Marked")

                    marked_today.add(name)

                conn.close()

    for (top, right, bottom, left) in face_locations:
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
        cv2.putText(frame, name, (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (0, 255, 0), 2)

    cv2.imshow("AI Attendance System", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

video_capture.release()
cv2.destroyAllWindows()

