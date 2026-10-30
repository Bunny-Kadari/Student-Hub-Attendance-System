# PostgreSQL schema is initialized automatically by app.py on first request.
# This file remains as a manual initialization entry point.
from app import init_db

if __name__ == "__main__":
    init_db()
    print("PostgreSQL schema initialized. No sample accounts or records were created.")
