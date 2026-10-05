import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import generate_password_hash, check_password_hash
from data_seed import BRANCHES, PERIODS, SUBJECTS, FACULTY, STUDENTS, TIMETABLE

app = Flask(__name__)
app.secret_key = "gecp-timetable-secret-change-me"  # only used to sign the login cookie

# FIXED FOR VERCEL: Uses writeable /tmp directory
DB_PATH = "/tmp/college.db"
DAY_ORDER = "CASE t.day WHEN 'Mon' THEN 1 WHEN 'Tue' THEN 2 WHEN 'Wed' THEN 3 WHEN 'Thu' THEN 4 WHEN 'Fri' THEN 5 END"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables and load the sample data, but only the first time this runs."""
    is_new = not os.path.exists(DB_PATH)
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        default_room TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS students (
        roll_no TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        branch_id INTEGER NOT NULL REFERENCES branches(id),
        batch TEXT NOT NULL,
        sr_no INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS subjects (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS faculty (
        code TEXT PRIMARY KEY,
        full_name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS periods (
        no INTEGER PRIMARY KEY,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS timetable (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        branch_id INTEGER NOT NULL REFERENCES branches(id),
        day TEXT NOT NULL,
        start_period INTEGER NOT NULL,
        end_period INTEGER NOT NULL,
        batch TEXT,
        subject_code TEXT NOT NULL REFERENCES subjects(code),
        faculty_code TEXT REFERENCES faculty(code),
        room TEXT
    );
    CREATE TABLE IF NOT EXISTS teacher_auth (
        code TEXT PRIMARY KEY REFERENCES faculty(code),
        password_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timetable_id INTEGER NOT NULL REFERENCES timetable(id),
        date TEXT NOT NULL,
        roll_no TEXT NOT NULL REFERENCES students(roll_no),
        status TEXT NOT NULL CHECK(status IN ('Present','Absent')),
        marked_by TEXT NOT NULL REFERENCES faculty(code),
        marked_at TEXT NOT NULL,
        UNIQUE(timetable_id, date, roll_no)
    );
    """)

    if is_new:
        cur.executemany("INSERT INTO branches VALUES (?,?,?)", BRANCHES)
        cur.executemany("INSERT INTO periods VALUES (?,?,?)", PERIODS)
        cur.executemany("INSERT INTO subjects VALUES (?,?)", SUBJECTS)
        cur.executemany("INSERT INTO faculty VALUES (?,?)", FACULTY)
        cur.executemany(
            "INSERT INTO students (roll_no, name, branch_id, batch, sr_no) VALUES (?,?,?,?,?)",
            [(roll, name, branch_i + 1, batch, sr) for roll, name, batch, sr, branch_i in STUDENTS],
        )
        cur.executemany(
            "INSERT INTO timetable (branch_id, day, start_period, end_period, batch, subject_code, faculty_code, room) "
            "VALUES (?,?,?,?,?,?,?,?)",
            TIMETABLE,
        )
        cur.executemany(
            "INSERT INTO teacher_auth (code, password_hash) VALUES (?,?)",
            [(code, generate_password_hash(code)) for code, _ in FACULTY],
        )
        conn.commit()
        print("Created college.db and loaded sample data.")
    conn.close()

# FIXED FOR VERCEL: Triggers DB creation automatically on import
init_db()

def require_login():
    code = session.get("faculty_code")
    if not code:
        return None
    return code


# ---------- student pages (existing) ----------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/student/<roll_no>")
def get_student(roll_no):
    roll_no = roll_no.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,6}", roll_no):
        return jsonify(error="Enter a valid roll number, like CE01."), 400

    conn = get_conn()
    try:
        student = conn.execute(
            "SELECT s.roll_no, s.name, s.batch, s.sr_no, b.id AS branch_id, b.name AS branch, "
            "  (SELECT COUNT(*) FROM students x WHERE x.branch_id = s.branch_id AND x.batch = s.batch) AS batch_size "
            "FROM students s JOIN branches b ON b.id = s.branch_id WHERE s.roll_no = ?",
            (roll_no,),
        ).fetchone()

        if not student:
            partial = conn.execute(
                "SELECT 1 FROM students WHERE roll_no LIKE ? LIMIT 1", (roll_no + "%",)
            ).fetchone() is not None
            return jsonify(error=f"No student found with roll number {roll_no}.", partial=partial), 404

        student = dict(student)
        branch_id = student.pop("branch_id")

        rows = conn.execute(
            "SELECT t.day, t.subject_code AS code, sub.name AS subject, "
            "  (t.batch IS NOT NULL) AS batch_specific, "
            "  COALESCE(f.full_name, t.faculty_code) AS faculty, "
            "  COALESCE(t.room, CASE WHEN t.subject_code IN ('LIB','SL') THEN NULL ELSE b.default_room END) AS room, "
            "  p1.start_time AS start, p2.end_time AS end "
            "FROM timetable t "
            "JOIN branches b ON b.id = t.branch_id "
            "JOIN subjects sub ON sub.code = t.subject_code "
            "LEFT JOIN faculty f ON f.code = t.faculty_code "
            "JOIN periods p1 ON p1.no = t.start_period "
            "JOIN periods p2 ON p2.no = t.end_period "
            "WHERE t.branch_id = ? AND (t.batch IS NULL OR t.batch = ?) "
            f"ORDER BY {DAY_ORDER}, t.start_period",
            (branch_id, student["batch"]),
        ).fetchall()

        timetable = {}
        for r in rows:
            r = dict(r)
            timetable.setdefault(r["day"], []).append({
                "code": r["code"], "subject": r["subject"], "faculty": r["faculty"],
                "room": r["room"], "batch_specific": bool(r["batch_specific"]),
                "start": r["start"], "end": r["end"],
            })
        return jsonify(student=student, timetable=timetable)
    finally:
        conn.close()


@app.route("/api/student/<roll_no>/attendance")
def student_attendance(roll_no):
    roll_no = roll_no.strip().upper()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify(error="Month should look like 2026-09."), 400
    like = month + "-%"

    conn = get_conn()
    try:
        student = conn.execute(
            "SELECT roll_no, name, branch_id, batch FROM students WHERE roll_no=?", (roll_no,)
        ).fetchone()
        if not student:
            return jsonify(error=f"No student found with roll number {roll_no}."), 404

        rows = conn.execute(
            "SELECT t.id AS timetable_id, t.subject_code AS code, sub.name AS subject, "
            "  COALESCE(h.held, 0) AS held, COALESCE(a.attended, 0) AS attended "
            "FROM timetable t "
            "JOIN subjects sub ON sub.code = t.subject_code "
            "LEFT JOIN (SELECT timetable_id, COUNT(DISTINCT date) AS held FROM attendance "
            "           WHERE date LIKE ? GROUP BY timetable_id) h ON h.timetable_id = t.id "
            "LEFT JOIN (SELECT timetable_id, COUNT(*) AS attended FROM attendance "
            "           WHERE roll_no = ? AND status = 'Present' AND date LIKE ? GROUP BY timetable_id) a "
            "  ON a.timetable_id = t.id "
            "WHERE t.branch_id = ? AND (t.batch IS NULL OR t.batch = ?)",
            (like, roll_no, like, student["branch_id"], student["batch"]),
        ).fetchall()

        by_subject = {}
        for r in rows:
            e = by_subject.setdefault(r["code"], {"code": r["code"], "subject": r["subject"], "held": 0, "attended": 0})
            e["held"] += r["held"]
            e["attended"] += r["attended"]

        report = sorted(by_subject.values(), key=lambda e: e["subject"])
        for e in report:
            e["percent"] = round(e["attended"] * 100 / e["held"], 1) if e["held"] else None

        totals = {
            "held": sum(e["held"] for e in report),
            "attended": sum(e["attended"] for e in report),
        }
        totals["percent"] = round(totals["attended"] * 100 / totals["held"], 1) if totals["held"] else None

        return jsonify(month=month, roll_no=roll_no, report=report, totals=totals)
    finally:
        conn.close()


# ---------- faculty portal ----------

@app.route("/faculty")
def faculty_page():
    return render_template("faculty.html")


@app.route("/api/faculty/session")
def faculty_session():
    code = require_login()
    if not code:
        return jsonify(logged_in=False)
    conn = get_conn()
    try:
        row = conn.execute("SELECT full_name FROM faculty WHERE code=?", (code,)).fetchone()
    finally:
        conn.close()
    return jsonify(logged_in=True, code=code, name=row["full_name"] if row else code)


@app.route("/api/faculty/login", methods=["POST"])
def faculty_login():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().upper()
    password = str(body.get("password", ""))
    conn = get_conn()
    try:
        row = conn.execute("SELECT password_hash FROM teacher_auth WHERE code=?", (code,)).fetchone()
    finally:
        conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify(error="Code or password is incorrect."), 401
    session["faculty_code"] = code
    return jsonify(ok=True, code=code)


@app.route("/api/faculty/logout", methods=["POST"])
def faculty_logout():
