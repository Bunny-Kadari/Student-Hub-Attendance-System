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
    return render_template("admin.html", users=users, students=students, departments=departments)

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
            cur.execute("DELETE FROM students WHERE id=%s", (student_id,))
    if row:
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(row["hub_id"]) + ".jpg")
        if os.path.exists(image_path):
            os.remove(image_path)
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
            if request.method == "POST":
                cur.execute("UPDATE students SET name=%s,semester=%s,section=%s WHERE id=%s",
                            (request.form["name"],request.form["semester"],request.form.get("section",""),student_id))
                photo = request.files.get("photo")
                if photo and photo.filename:
                    photo.save(os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(student["hub_id"])+".jpg"))
                return redirect(url_for("student_list"))
    return render_template("edit_student.html", student=student)


@app.route("/admin/department/add", methods=["POST"])
def add_department():
    if not admin_required():
        return redirect(url_for("login"))
    dept_name = request.form.get("dept_name", "").strip().upper()
    if not dept_name:
        flash("Department name is required.", "danger")
        return redirect(url_for("admin_panel"))
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO departments(dept_name) VALUES (%s)", (dept_name,))
        flash("Department added.", "success")
    except psycopg.errors.UniqueViolation:
        flash("Department already exists.", "danger")
    return redirect(url_for("admin_panel"))

@app.route("/admin/class/add", methods=["POST"])
def add_class():
    if not admin_required():
        return redirect(url_for("login"))
    code = request.form.get("subject_code", "").strip().upper()
    name = request.form.get("subject_name", "").strip()
    dept_id = request.form.get("dept_id")
    semester = request.form.get("semester")
    faculty_id = request.form.get("faculty_id")
    section = request.form.get("section", "").strip()
    if not all([code, name, dept_id, semester, faculty_id]):
        flash("Complete all subject and class fields.", "danger")
        return redirect(url_for("admin_panel"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO subjects(subject_code,subject_name,dept_id,semester)
                           VALUES (%s,%s,%s,%s) RETURNING id""", (code,name,dept_id,semester))
            subject_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO classes(subject_id,faculty_id,semester,section)
                           VALUES (%s,%s,%s,%s)""", (subject_id,faculty_id,semester,section))
    flash("Subject created and class assigned.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:user_id>/reset-password", methods=["POST"])
def reset_faculty_password(user_id):
    if not admin_required():
        return redirect(url_for("login"))
    password = request.form.get("password", "")
    if len(password) < 10:
        flash("New password must contain at least 10 characters.", "danger")
        return redirect(url_for("admin_panel"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash=%s WHERE id=%s AND role='faculty'",
                        (generate_password_hash(password),user_id))
            changed = cur.rowcount
    flash("Faculty password updated." if changed else "Faculty account not found.", "success" if changed else "danger")
    return redirect(url_for("admin_panel"))

@app.route("/admin/student/<int:student_id>/reset-password", methods=["POST"])
def reset_student_password(student_id):
    if not admin_required():
        return redirect(url_for("login"))
    password = request.form.get("password", "")
    if len(password) < 10:
        flash("New password must contain at least 10 characters.", "danger")
        return redirect(url_for("admin_panel"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE students SET password_hash=%s WHERE id=%s",
                        (generate_password_hash(password),student_id))
            changed = cur.rowcount
    flash("Student password updated." if changed else "Student not found.", "success" if changed else "danger")
    return redirect(url_for("admin_panel"))

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    is_student = "student_id" in session
    is_user = "user_id" in session
    if not (is_student or is_user):
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new) < 10:
            error = "New password must contain at least 10 characters."
        elif new != confirm:
            error = "New passwords do not match."
        else:
            table, key, record_id = ("students","id",session["student_id"]) if is_student else ("users","id",session["user_id"])
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT password_hash FROM {table} WHERE {key}=%s", (record_id,))
                    row = cur.fetchone()
                    if not row or not check_password_hash(row["password_hash"], current):
                        error = "Current password is incorrect."
                    else:
                        cur.execute(f"UPDATE {table} SET password_hash=%s WHERE {key}=%s",
                                    (generate_password_hash(new),record_id))
            if not error:
                flash("Password changed successfully.", "success")
                return redirect(url_for("student_dashboard" if is_student else "dashboard"))
    return render_template("change_password.html", error=error)

@app.route("/attendance/<int:class_id>", methods=["GET", "POST"])
def attendance(class_id):
    if not login_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if session.get("role") != "admin":
                cur.execute("SELECT 1 FROM classes WHERE id=%s AND faculty_id=%s",
                            (class_id, session.get("user_id")))
                if not cur.fetchone():
                    return "Access denied", 403
            cur.execute("SELECT id,name FROM students ORDER BY name")
            students = cur.fetchall()
            if request.method == "POST":
                selected_date = request.form["date"]
                for student in students:
                    status = request.form.get(f"status_{student['id']}")
                    if status in ("Present","Absent"):
                        cur.execute("""INSERT INTO attendance(student_id,class_id,date,status)
                                       VALUES (%s,%s,%s,%s)
                                       ON CONFLICT(student_id,class_id,date)
                                       DO UPDATE SET status=EXCLUDED.status""",
                                    (student["id"],class_id,selected_date,status))
    return render_template("attendance.html", students=students, class_id=class_id,
                           success=request.method == "POST")

@app.route("/view_report/<int:class_id>")
def view_report(class_id):
    if not login_required():
        return redirect(url_for("login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if session.get("role") != "admin":
                cur.execute("SELECT 1 FROM classes WHERE id=%s AND faculty_id=%s",
                            (class_id, session.get("user_id")))
                if not cur.fetchone():
                    return "Access denied", 403
            cur.execute("""SELECT students.name,attendance.date,attendance.status
                           FROM attendance JOIN students ON attendance.student_id=students.id
                           WHERE attendance.class_id=%s ORDER BY attendance.date DESC""", (class_id,))
            records = cur.fetchall()
    return render_template("view_report.html", records=records)

@app.route("/student_login", methods=["GET","POST"])
def student_login():
    error = None
    if request.method == "POST":
        hub_id = request.form.get("hub_id","").strip()
        password = request.form.get("password","")
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM students WHERE hub_id=%s", (hub_id,))
                student = cur.fetchone()
        if student and check_password_hash(student["password_hash"], password):
            session.clear()
            session["student_id"] = student["id"]
            session["student_name"] = student["name"]
            session["role"] = "student"
            return redirect(url_for("student_dashboard"))
        error = "Invalid Hub ID or password."
    return render_template("student_login.html", error=error)

@app.route("/student_dashboard")
def student_dashboard():
    if "student_id" not in session:
        return redirect(url_for("student_login"))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM attendance WHERE student_id=%s",
                        (session["student_id"],))
            total_classes = cur.fetchone()["n"] or 0
            cur.execute("SELECT COUNT(*) AS n FROM attendance WHERE student_id=%s AND status='Present'",
                        (session["student_id"],))
            total_present = cur.fetchone()["n"] or 0
            cur.execute("SELECT date,status FROM attendance WHERE student_id=%s ORDER BY date",
                        (session["student_id"],))
            records = cur.fetchall()
    percentage = round((total_present / total_classes) * 100, 2) if total_classes else 0
    dates = [str(r["date"]) for r in records]
    status_values = [1 if r["status"] == "Present" else 0 for r in records]
    return render_template("student_dashboard.html", name=session["student_name"],
                           total_classes=total_classes,total_present=total_present,
                           percentage=percentage,records=records,dates=dates,status_values=status_values)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

def get_class_access(class_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT c.id,c.semester,c.section,s.dept_id,s.subject_name
                           FROM classes c JOIN subjects s ON s.id=c.subject_id
                           WHERE c.id=%s AND (%s='admin' OR c.faculty_id=%s)""",
                        (class_id, session.get("role"), session.get("user_id")))
            return cur.fetchone()


@app.route("/start_ai/<int:class_id>")
def start_ai(class_id):
    if not login_required():
        return redirect(url_for("login"))
    class_info = get_class_access(class_id)
    if not class_info:
        return "Access denied", 403
    return render_template("face_attendance.html", class_info=class_info)


@app.route("/api/face-roster/<int:class_id>")
def face_roster(class_id):
    if not login_required():
        return {"error": "Login required."}, 401
    class_info = get_class_access(class_id)
    if not class_info:
        return {"error": "Access denied."}, 403
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT st.id,st.hub_id,st.name,fd.descriptor
                           FROM students st JOIN face_data fd ON fd.student_id=st.id
                           WHERE st.dept_id=%s AND st.semester=%s
                             AND COALESCE(st.section,'')=COALESCE(%s,'')
                             AND fd.descriptor IS NOT NULL ORDER BY st.name""",
                        (class_info["dept_id"],class_info["semester"],class_info["section"]))
            rows = cur.fetchall()
    return {"students":[{"id":r["id"],"hub_id":r["hub_id"],"name":r["name"],"descriptor":r["descriptor"]} for r in rows],
            "registered_count":len(rows)}


@app.route("/api/face-attendance/<int:class_id>", methods=["POST"])
def save_face_attendance(class_id):
    if not login_required():
        return {"error":"Login required."},401
    class_info=get_class_access(class_id)
    if not class_info:
        return {"error":"Access denied."},403
    payload=request.get_json(silent=True) or {}
    ids=payload.get("recognized_ids",[])
    if not isinstance(ids,list) or len(ids)>500:
        return {"error":"Invalid attendance list."},400
    try:
        ids=list({int(value) for value in ids})
    except (TypeError,ValueError):
        return {"error":"Invalid student IDs."},400
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id FROM students WHERE dept_id=%s AND semester=%s
                           AND COALESCE(section,'')=COALESCE(%s,'')""",
                        (class_info["dept_id"],class_info["semester"],class_info["section"]))
            eligible={row["id"] for row in cur.fetchall()}
            if not set(ids).issubset(eligible):
                return {"error":"A student does not belong to this class."},400
            cur.execute("""INSERT INTO attendance(student_id,class_id,date,status)
                           SELECT id,%s,CURRENT_DATE,'Absent' FROM students
                           WHERE dept_id=%s AND semester=%s AND COALESCE(section,'')=COALESCE(%s,'')
                           ON CONFLICT(student_id,class_id,date) DO UPDATE SET status='Absent'""",
                        (class_id,class_info["dept_id"],class_info["semester"],class_info["section"]))
            if ids:
                cur.execute("""UPDATE attendance SET status='Present'
                               WHERE class_id=%s AND date=CURRENT_DATE AND student_id=ANY(%s)""",
                            (class_id,ids))
    return {"status":"saved","present_count":len(ids),"class_strength":len(eligible),"message":"Attendance saved."}


@app.route("/health")
def health():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
