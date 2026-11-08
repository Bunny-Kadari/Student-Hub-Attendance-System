import os
import hmac
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY is required. Configure it in Render Environment Variables before starting the app.")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

ADMIN_SETUP_TOKEN = os.environ.get("ADMIN_SETUP_TOKEN", "")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "known_faces")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured. Add your PostgreSQL connection string in the service environment.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)

def init_db():
    """Create a clean schema. No sample accounts or sample student records are inserted."""
    statements = [
        """CREATE TABLE IF NOT EXISTS departments (
            id BIGSERIAL PRIMARY KEY,
            dept_name TEXT NOT NULL UNIQUE
        )""",
        """CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin','faculty')),
            dept_id BIGINT REFERENCES departments(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS students (
            id BIGSERIAL PRIMARY KEY,
            hub_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            dept_id BIGINT NOT NULL REFERENCES departments(id),
            semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 8),
            section TEXT,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS subjects (
            id BIGSERIAL PRIMARY KEY,
            subject_code TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            dept_id BIGINT NOT NULL REFERENCES departments(id),
            semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 8)
        )""",
        """CREATE TABLE IF NOT EXISTS classes (
            id BIGSERIAL PRIMARY KEY,
            subject_id BIGINT NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            faculty_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 8),
            section TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS attendance (
            id BIGSERIAL PRIMARY KEY,
            student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            class_id BIGINT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('Present','Absent')),
            UNIQUE(student_id, class_id, date)
        )""",
        """CREATE TABLE IF NOT EXISTS face_data (
            id BIGSERIAL PRIMARY KEY,
            student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            dataset_path TEXT NOT NULL,
            descriptor JSONB
        )"""
    ]
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Serialize first-time schema creation across Gunicorn workers.
            cur.execute("SELECT pg_advisory_xact_lock(2026103001)")
            for statement in statements:
                cur.execute(statement)
            # Safe additive migration for existing databases.
            cur.execute("ALTER TABLE face_data ADD COLUMN IF NOT EXISTS descriptor JSONB")

def scalar(query, params=()):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return next(iter(row.values())) if row else None

def admin_exists():
    return bool(scalar("SELECT EXISTS(SELECT 1 FROM users WHERE role='admin') AS has_admin"))

@app.before_request
def ensure_schema():
    # Idempotent schema creation makes a fresh Render database usable on first request.
    # A small process-local flag avoids running DDL on every request.
    if not getattr(app, "_schema_ready", False):
        init_db()
        app._schema_ready = True

@app.route("/")
def home():
    return render_template("home.html", setup_needed=not admin_exists())

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if admin_exists():
        return redirect(url_for("login"))
    if not ADMIN_SETUP_TOKEN:
        return "Initial setup is locked. Configure ADMIN_SETUP_TOKEN in Render Environment Variables first.", 503
    if request.method == "POST":
        submitted_token = request.form.get("setup_token", "")
        if not hmac.compare_digest(submitted_token, ADMIN_SETUP_TOKEN):
            return render_template("setup.html", error="Invalid setup token.")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(username) < 4 or len(password) < 10:
            return render_template("setup.html", error="Username must be at least 4 characters and password at least 10 characters.")
        if password != confirm:
            return render_template("setup.html", error="Passwords do not match.")
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Conditional insertion ensures only the first visitor can create the initial admin.
                    cur.execute("LOCK TABLE users IN EXCLUSIVE MODE")
                    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin'")
                    if cur.fetchone()["n"]:
                        return redirect(url_for("login"))
                    cur.execute(
                        "INSERT INTO users (username,password_hash,role) VALUES (%s,%s,'admin')",
                        (username, generate_password_hash(password))
                    )
            session.clear()
            session["user_id"] = scalar("SELECT id FROM users WHERE username=%s", (username,))
            session["username"] = username
            session["role"] = "admin"
            return redirect(url_for("dashboard"))
        except psycopg.errors.UniqueViolation:
            return render_template("setup.html", error="That username is already taken.")
    return render_template("setup.html", error=None)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username=%s AND role IN ('admin','faculty')", (username,))
                user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["faculty_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"] if user["role"] == "admin" else "faculty"
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

def login_required():
    return "user_id" in session and session.get("role") in ("admin", "faculty")

def admin_required():
    return login_required() and session.get("role") == "admin"

@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if session["role"] == "admin":
                cur.execute("""SELECT subjects.subject_name, classes.id
                               FROM classes JOIN subjects ON classes.subject_id=subjects.id
                               ORDER BY subjects.subject_name""")
            else:
                cur.execute("""SELECT subjects.subject_name, classes.id
                               FROM classes JOIN subjects ON classes.subject_id=subjects.id
                               WHERE classes.faculty_id=%s ORDER BY subjects.subject_name""",
                            (session["user_id"],))
            classes = cur.fetchall()
    return render_template("dashboard.html", classes=classes,
                           username=session["username"], role=session["role"])

@app.route("/admin")
def admin_panel():
    if not admin_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id,u.username,u.role,d.dept_name
                           FROM users u LEFT JOIN departments d ON u.dept_id=d.id
                           ORDER BY u.role,u.username""")
            users = cur.fetchall()
            cur.execute("""SELECT s.id,s.hub_id,s.name,d.dept_name,s.semester,s.section
                           FROM students s JOIN departments d ON s.dept_id=d.id
                           ORDER BY s.name""")
            students = cur.fetchall()
            cur.execute("SELECT id,dept_name FROM departments ORDER BY dept_name")
            departments = cur.fetchall()
            cur.execute("""SELECT c.id,c.semester,c.section,s.subject_code,s.subject_name,
                                  s.dept_id,d.dept_name,u.id AS faculty_id,u.username AS faculty_name
                           FROM classes c
                           JOIN subjects s ON s.id=c.subject_id
                           JOIN departments d ON d.id=s.dept_id
                           JOIN users u ON u.id=c.faculty_id
                           ORDER BY s.subject_name,u.username""")
            classes = cur.fetchall()
    return render_template("admin.html", users=users, students=students,
                           departments=departments, classes=classes)

@app.route("/admin/faculty/add", methods=["POST"])
def add_faculty():
    if not admin_required():
        return redirect(url_for("login"))
    username = request.form.get("username","").strip()
    password = request.form.get("password","")
    dept_id = request.form.get("dept_id") or None
    if len(username) < 4 or len(password) < 10:
        flash("Faculty username must be 4+ characters and password 10+ characters.", "danger")
        return redirect(url_for("admin_panel"))
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO users(username,password_hash,role,dept_id) VALUES (%s,%s,'faculty',%s)",
                            (username,generate_password_hash(password),dept_id))
        flash("Faculty account created.", "success")
    except psycopg.errors.UniqueViolation:
        flash("Username already exists.", "danger")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    if not admin_required():
        return redirect(url_for("login"))
    if user_id == session["user_id"]:
        flash("You cannot delete the currently signed-in Admin account.", "danger")
        return redirect(url_for("admin_panel"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s AND role='faculty'", (user_id,))
    flash("Faculty account deleted.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/student/add", methods=["POST"])
def add_student():
    if not admin_required():
        return redirect(url_for("login"))
    hub_id = request.form.get("hub_id","").strip()
    name = request.form.get("name","").strip()
    dept_id = request.form.get("dept_id")
    semester = request.form.get("semester")
    section = request.form.get("section","").strip()
    password = request.form.get("password","")
    if not all([hub_id,name,dept_id,semester]) or len(password) < 10:
        flash("Complete all required fields. Student password must be at least 10 characters.", "danger")
        return redirect(url_for("admin_panel"))
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO students(hub_id,name,dept_id,semester,section,password_hash)
                               VALUES (%s,%s,%s,%s,%s,%s)""",
                            (hub_id,name,dept_id,semester,section,generate_password_hash(password)))
        photo = request.files.get("photo")
        if photo and photo.filename:
            filename = secure_filename(hub_id) + ".jpg"
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        flash("Student account created.", "success")
    except psycopg.errors.UniqueViolation:
        flash("That Hub ID already exists.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/student/<int:student_id>/face")
def register_student_face(student_id):
    if not admin_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,hub_id,name FROM students WHERE id=%s", (student_id,))
            student = cur.fetchone()
    if not student:
        return "Student not found", 404
    return render_template("face_register.html", student=student)


@app.route("/api/admin/student/<int:student_id>/face", methods=["POST"])
def save_student_face(student_id):
    if not admin_required():
        return {"error": "Admin login required."}, 401
    payload = request.get_json(silent=True) or {}
    descriptor = payload.get("descriptor")
    if not isinstance(descriptor, list) or len(descriptor) != 128:
        return {"error": "A valid 128-value face descriptor is required."}, 400
    try:
        values = [float(value) for value in descriptor]
    except (TypeError, ValueError):
        return {"error": "Face descriptor contains invalid values."}, 400
    import math
    if not all(math.isfinite(value) for value in values):
        return {"error": "Face descriptor contains invalid values."}, 400
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM students WHERE id=%s", (student_id,))
            if not cur.fetchone():
                return {"error": "Student not found."}, 404
            cur.execute("""UPDATE face_data
                           SET descriptor=%s,dataset_path=%s
                           WHERE id=(SELECT id FROM face_data WHERE student_id=%s
                                     ORDER BY id DESC LIMIT 1)""",
                        (Jsonb(values), "browser-face-api", student_id))
            if cur.rowcount == 0:
                cur.execute("""INSERT INTO face_data(student_id,dataset_path,descriptor)
                               VALUES (%s,%s,%s)""",
                            (student_id, "browser-face-api", Jsonb(values)))
    return {"status": "saved", "message": "Face data registered successfully."}


@app.route("/students")
def student_list():
    if not admin_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.*,d.dept_name FROM students s
                           LEFT JOIN departments d ON s.dept_id=d.id ORDER BY s.id DESC""")
            students = cur.fetchall()
    return render_template("student_list.html", students=students)

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if not admin_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hub_id FROM students WHERE id=%s", (student_id,))
            row = cur.fetchone()
            if row:
                # Attendance and face descriptors are removed by the students table's ON DELETE CASCADE.
                cur.execute("DELETE FROM students WHERE id=%s", (student_id,))
    if row:
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(row["hub_id"]) + ".jpg")
        if os.path.exists(image_path):
            os.remove(image_path)
        flash("Student account, face descriptor and attendance records deleted.", "warning")
    else:
        flash("Student not found.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/edit_student/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    if not admin_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM students WHERE id=%s", (student_id,))
            student = cur.fetchone()
            if not student:
                return "Student not found", 404
            cur.execute("SELECT id,dept_name FROM departments ORDER BY dept_name")
            departments = cur.fetchall()
            if request.method == "POST":
                hub_id = request.form.get("hub_id", "").strip()
                name = request.form.get("name", "").strip()
                dept_id = request.form.get("dept_id")
                semester = request.form.get("semester")
                section = request.form.get("section", "").strip()
                if not all([hub_id, name, dept_id, semester]):
                    flash("Hub ID, name, department and semester are required.", "danger")
                    return render_template("edit_student.html", student=student, departments=departments)
                try:
                    cur.execute(
                        """UPDATE students SET hub_id=%s,name=%s,dept_id=%s,semester=%s,section=%s
                           WHERE id=%s""",
                        (hub_id, name, dept_id, semester, section, student_id)
                    )
                except psycopg.errors.UniqueViolation:
                    flash("That Hub ID is already assigned to another student.", "danger")
                    return render_template("edit_student.html", student=student, departments=departments)
                old_hub_id = student["hub_id"]
                old_photo = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(old_hub_id) + ".jpg")
                new_photo = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(hub_id) + ".jpg")
                photo = request.files.get("photo")
                if photo and photo.filename:
                    photo.save(new_photo)
                    if old_photo != new_photo and os.path.exists(old_photo):
                        os.remove(old_photo)
                elif old_photo != new_photo and os.path.exists(old_photo):
                    os.replace(old_photo, new_photo)
                flash("Student details updated successfully. Re-check class allocation if department, semester or section changed.", "success")
                return redirect(url_for("admin_panel"))
    return render_template("edit_student.html", student=student, departments=departments)

