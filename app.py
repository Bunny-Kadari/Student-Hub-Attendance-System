from flask import Flask, render_template, request, redirect, session
import os
from werkzeug.utils import secure_filename
import sqlite3
import subprocess

app = Flask(__name__)
app.secret_key = "secretkey123"

# ---------------- BASE DIRECTORY ----------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "hub.db")

# ---------------- PHOTO UPLOAD CONFIG ----------------
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "known_faces")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ---------------- DATABASE ----------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("home.html")


# ---------------- FACULTY LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        with get_db_connection() as conn:
            faculty = conn.execute(
                "SELECT * FROM faculty WHERE username=? AND password=?",
                (username, password)
            ).fetchone()

        if faculty:
            session["faculty_id"] = faculty["id"]
            session["username"] = faculty["username"]
            session["role"] = faculty["role"]
            return redirect("/dashboard")
        else:
            error = "Invalid username or password ❌"

    return render_template("login.html", error=error)


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "faculty_id" not in session:
        return redirect("/login")

    with get_db_connection() as conn:
        classes = conn.execute("""
            SELECT subjects.subject_name, classes.id
            FROM classes
            JOIN subjects ON classes.subject_id = subjects.id
            WHERE classes.faculty_id = ?
        """, (session["faculty_id"],)).fetchall()

    return render_template(
        "dashboard.html",
        classes=classes,
        username=session["username"],
        role=session.get("role")
    )


# ---------------- ADD STUDENT ----------------
@app.route("/add_student", methods=["GET", "POST"])
def add_student():

    if session.get("role") != "head":
        return "Access Denied ❌"

    message = None

    if request.method == "POST":
        hub_id = request.form["hub_id"]
        name = request.form["name"]
        dept_id = request.form["dept_id"]
        semester = request.form["semester"]
        section = request.form["section"]
        photo = request.files.get("photo")

        if not hub_id or not name or not dept_id or not semester:
            message = "All fields are required ❌"
            return render_template("add_student.html", message=message)

        try:
            with get_db_connection() as conn:
                conn.execute("""
                    INSERT INTO students (hub_id, name, dept_id, semester, section)
                    VALUES (?, ?, ?, ?, ?)
                """, (hub_id, name, int(dept_id), int(semester), section))
                conn.commit()

            if photo and photo.filename != "":
                filename = secure_filename(hub_id + ".jpg")
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

            message = "Student Added Successfully ✅"

        except sqlite3.IntegrityError:
            message = "Hub ID already exists ❌"

    return render_template("add_student.html", message=message)


# ---------------- STUDENT LIST ----------------
@app.route("/students")
def student_list():

    if "faculty_id" not in session:
        return redirect("/login")

    if session.get("role") != "head":
        return "Access Denied ❌"

    with get_db_connection() as conn:
        students = conn.execute("""
            SELECT students.*, departments.dept_name as dept_name
            FROM students
            LEFT JOIN departments
            ON students.dept_id = departments.id
            ORDER BY students.id DESC
        """).fetchall()

    return render_template("student_list.html", students=students)


# ---------------- DELETE STUDENT ----------------
@app.route("/delete_student/<int:student_id>")
def delete_student(student_id):

    if session.get("role") != "head":
        return "Access Denied ❌"

    with get_db_connection() as conn:
        student = conn.execute(
            "SELECT hub_id FROM students WHERE id=?",
            (student_id,)
        ).fetchone()

        if student:
            image_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                student["hub_id"] + ".jpg"
            )
            if os.path.exists(image_path):
                os.remove(image_path)

        conn.execute("DELETE FROM students WHERE id=?", (student_id,))
        conn.commit()

    return redirect("/students")

# ---------------- EDIT STUDENT ----------------
@app.route("/edit_student/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):

    if session.get("role") != "head":
        return "Access Denied ❌"

    conn = get_db_connection()

    student = conn.execute(
        "SELECT * FROM students WHERE id=?",
        (student_id,)
    ).fetchone()

    if request.method == "POST":

        name = request.form["name"]
        semester = request.form["semester"]
        section = request.form["section"]
        photo = request.files.get("photo")

        conn.execute("""
            UPDATE students
            SET name=?, semester=?, section=?
            WHERE id=?
        """, (name, semester, section, student_id))

        conn.commit()

        # -------- MANUAL PHOTO UPLOAD --------
        if photo and photo.filename != "":
            filename = student["hub_id"] + ".jpg"
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        # -------- WEBCAM PHOTO SAVE --------
        if os.path.exists("camera.jpg"):
            os.replace(
                "camera.jpg",
                os.path.join(app.config["UPLOAD_FOLDER"],
                             student["hub_id"] + ".jpg")
            )

        conn.close()

        return redirect("/students")

    conn.close()
    return render_template("edit_student.html", student=student)

# ---------------- CAPTURE PHOTO (WEBCAM) ----------------
@app.route("/capture_photo/<int:student_id>")
def capture_photo(student_id):

    script_path = os.path.join(BASE_DIR, "capture_photo.py")

    subprocess.Popen([
        "gnome-terminal",
        "--",
        "bash",
        "-c",
        f"python3 {script_path}; exec bash"
    ])

    return redirect(f"/edit_student/{student_id}")


# ---------------- MANUAL ATTENDANCE ----------------
@app.route("/attendance/<int:class_id>", methods=["GET", "POST"])
def attendance(class_id):

    if "faculty_id" not in session:
        return redirect("/login")

    success = False
    conn = get_db_connection()

    students = conn.execute("""
        SELECT id, name FROM students
        ORDER BY name ASC
    """).fetchall()

    if request.method == "POST":
        selected_date = request.form["date"]

        for student in students:
            status = request.form.get(f"status_{student['id']}")

            if status:
                existing = conn.execute("""
                    SELECT id FROM attendance
                    WHERE student_id=? AND class_id=? AND date=?
                """, (student["id"], class_id, selected_date)).fetchone()

                if existing:
                    conn.execute("""
                        UPDATE attendance
                        SET status=?
                        WHERE id=?
                    """, (status, existing["id"]))
                else:
                    conn.execute("""
                        INSERT INTO attendance (student_id, class_id, date, status)
                        VALUES (?, ?, ?, ?)
                    """, (
                        student["id"],
                        class_id,
                        selected_date,
                        status
                    ))

        conn.commit()
        success = True

    conn.close()

    return render_template(
        "attendance.html",
        students=students,
        class_id=class_id,
        success=success
    )


# ---------------- AI ATTENDANCE ----------------
@app.route("/start_ai/<int:class_id>")
def start_ai(class_id):

    script_path = os.path.join(BASE_DIR, "attendance_system.py")
    venv_python = os.path.join(BASE_DIR, "venv", "bin", "python")

    command = f"{venv_python} {script_path} {class_id}; exec bash"

    subprocess.Popen([
        "gnome-terminal",
        "--",
        "bash",
        "-c",
        command
    ])

    return redirect("/dashboard")


# ---------------- VIEW REPORT ----------------
@app.route("/view_report/<int:class_id>")
def view_report(class_id):

    if "faculty_id" not in session:
        return redirect("/login")

    with get_db_connection() as conn:
        records = conn.execute("""
            SELECT students.name, attendance.date, attendance.status
            FROM attendance
            JOIN students ON attendance.student_id = students.id
            WHERE attendance.class_id = ?
            ORDER BY attendance.date DESC
        """, (class_id,)).fetchall()

    return render_template("view_report.html", records=records)


# ---------------- STUDENT LOGIN ----------------
@app.route("/student_login", methods=["GET", "POST"])
def student_login():
    error = None

    if request.method == "POST":
        hub_id = request.form["hub_id"]

        with get_db_connection() as conn:
            student = conn.execute(
                "SELECT * FROM students WHERE hub_id=?",
                (hub_id,)
            ).fetchone()

        if student:
            session["student_id"] = student["id"]
            session["student_name"] = student["name"]
            return redirect("/student_dashboard")
        else:
            error = "Invalid Hub ID ❌"

    return render_template("student_login.html", error=error)


# ---------------- STUDENT DASHBOARD ----------------
@app.route("/student_dashboard")
def student_dashboard():

    if "student_id" not in session:
        return redirect("/student_login")

    with get_db_connection() as conn:

        total_classes = conn.execute("""
            SELECT COUNT(DISTINCT date)
            FROM attendance
            WHERE student_id = ?
        """, (session["student_id"],)).fetchone()[0] or 0

        total_present = conn.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE student_id = ? AND status='Present'
        """, (session["student_id"],)).fetchone()[0] or 0

        records = conn.execute("""
            SELECT date, status
            FROM attendance
            WHERE student_id = ?
            ORDER BY date ASC
        """, (session["student_id"],)).fetchall()

    percentage = 0
    if total_classes > 0:
        percentage = round((total_present / total_classes) * 100, 2)

    dates = [record["date"] for record in records]
    status_values = [1 if record["status"] == "Present" else 0 for record in records]

    return render_template(
        "student_dashboard.html",
        name=session["student_name"],
        total_classes=total_classes,
        total_present=total_present,
        percentage=percentage,
        records=records,
        dates=dates,
        status_values=status_values
    )


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True, port=5001)

