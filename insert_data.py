import sqlite3

conn = sqlite3.connect("hub.db")
cursor = conn.cursor()

# Insert Departments
cursor.execute("INSERT OR IGNORE INTO departments (dept_name) VALUES ('CSE')")
cursor.execute("INSERT OR IGNORE INTO departments (dept_name) VALUES ('ECE')")

# Insert Faculty
cursor.execute("""
INSERT OR IGNORE INTO faculty (username, password, dept_id)
VALUES ('cse_faculty', '1234', 1)
""")

# Insert Students
cursor.execute("""
INSERT OR IGNORE INTO students (hub_id, name, dept_id, semester, section)
VALUES ('21CSE001', 'Bunny', 1, 3, 'A')
""")

cursor.execute("""
INSERT OR IGNORE INTO students (hub_id, name, dept_id, semester, section)
VALUES ('21CSE002', 'Rahul', 1, 3, 'A')
""")

# Insert Subjects
cursor.execute("""
INSERT OR IGNORE INTO subjects (subject_code, subject_name, dept_id, semester)
VALUES ('CS201', 'Data Structures', 1, 3)
""")

conn.commit()
conn.close()

print("Sample data inserted successfully!")

