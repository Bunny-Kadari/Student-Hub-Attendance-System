# RGUKT Student Hub Attendance System

A Flask-based attendance management system with Admin, Faculty and Student accounts, manual attendance entry, class assignment and student attendance reports.

## Fresh deployment architecture

- Flask + Gunicorn on Render.
- PostgreSQL database through the `DATABASE_URL` environment variable.
- Passwords are stored as secure Werkzeug password hashes.
- No sample users, passwords, students, faculty, or attendance records are inserted.
- The first Admin account is created once through a setup page protected by your private `ADMIN_SETUP_TOKEN`.

## Deploy on Render

1. Create a PostgreSQL database with a provider such as Neon. Copy its PostgreSQL connection string. Use an SSL-enabled connection string.
2. In Render, create a new **Web Service** from this repository and select branch `fresh-postgres-admin-controlled`.
3. Build command: `pip install -r requirements.txt`.
4. Start command: `gunicorn --workers 2 --bind 0.0.0.0:$PORT app:app` (the included Procfile already contains this command).
5. Add these environment variables in Render:
   - `DATABASE_URL`: the PostgreSQL connection string.
   - `SECRET_KEY`: a long random secret. Generate one locally with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
   - `ADMIN_SETUP_TOKEN`: a long private token that only you know. You will enter this token once on the first setup page.
6. Deploy. On the first visit, choose **Initial Admin Setup**, enter your private setup token, and choose your own Admin username and password.
7. After logging in, use the Admin Control Panel in this order:
   1. Add Departments (for example CSE, ECE).
   2. Create Faculty accounts and choose their usernames/passwords.
   3. Create subjects and assign each class to a faculty member.
   4. Create Student accounts, setting each Hub ID and password.
8. Faculty sign in with username/password. Students sign in with Hub ID/password.

## Account control

- The first Admin is selected by you; the setup form is protected by `ADMIN_SETUP_TOKEN`.
- Public Admin setup closes as soon as the first Admin is created.
- Only Admin can add/delete Faculty and add/edit/delete Students.
- Admin can reset Faculty and Student passwords.
- Admin, Faculty and Students can change their own password.
- Passwords are never stored as plain text.
- There are no default credentials. Do not share your Admin password or setup token.

## Local development

Set the three environment variables first, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='your-postgresql-connection-string'
export SECRET_KEY='your-long-random-secret'
export ADMIN_SETUP_TOKEN='your-private-one-time-setup-token'
python3 app.py
```

Open `http://127.0.0.1:5001`.

## Important deployment notes

- The old SQLite `hub.db` is not imported. The new PostgreSQL database starts with empty tables. Old sample logins and seed records are not used.
- `insert_data.py` intentionally inserts no sample data.
- Render's local filesystem is not persistent on a Free Web Service. Student photo uploads are currently stored locally and may disappear after a redeploy/restart; configure persistent object storage before depending on those photos in production.
- The `/start_ai/<class_id>` endpoint displays a notice. Live webcam face recognition is not hosted by this web deployment; browser/server webcam access needs a separate local or device-side implementation.

## Project author

Kadari Bunny · B.Tech CSE
