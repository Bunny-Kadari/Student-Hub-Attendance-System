import sqlite3

# Connect to database (creates hub.db file automatically)
conn = sqlite3.connect("hub.db")

cursor = conn.cursor()

# Create departments table
cursor.execute("""
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dept_name TEXT NOT NULL UNIQUE
);
""")

# Create students table
cursor.execute("""
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hub_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    dept_id INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    section TEXT,
    FOREIGN KEY(dept_id) REFERENCES departments(id)
);
""")

# Create faculty table
cursor.execute("""
CREATE TABLE IF NOT EXISTS faculty (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    dept_id INTEGER NOT NULL,
    FOREIGN KEY(dept_id) REFERENCES departments(id)
);
""")

# Create subjects table
cursor.execute("""
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_code TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    dept_id INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    FOREIGN KEY(dept_id) REFERENCES departments(id)
);
""")

# Create classes table
cursor.execute("""
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL,
    faculty_id INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    section TEXT,
    FOREIGN KEY(subject_id) REFERENCES subjects(id),
    FOREIGN KEY(faculty_id) REFERENCES faculty(id)
);
""")

# Create attendance table
cursor.execute("""
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(student_id) REFERENCES students(id),
    FOREIGN KEY(class_id) REFERENCES classes(id)
);
""")

# Create face_data table
cursor.execute("""
CREATE TABLE IF NOT EXISTS face_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    dataset_path TEXT NOT NULL,
    FOREIGN KEY(student_id) REFERENCES students(id)
);
""")

conn.commit()
conn.close()

print("Database and tables created successfully!")

