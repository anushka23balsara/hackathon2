import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import generate_password_hash, check_password_hash
from data_seed import BRANCHES, PERIODS, SUBJECTS, FACULTY, STUDENTS, TIMETABLE

app = Flask(__name__)
app.secret_key = "gecp-timetable-secret-change-me"  # only used to sign the login cookie
DB_PATH = os.path.join(os.path.dirname(__file__), "college.db")
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
        # Every professor's starting password is their own code (e.g. code "DAP" -> password "DAP").
        # They should tell you if they'd like it changed; there's a "change password" option once logged in.
        cur.executemany(
            "INSERT INTO teacher_auth (code, password_hash) VALUES (?,?)",
            [(code, generate_password_hash(code)) for code, _ in FACULTY],
        )
        conn.commit()
        print("Created college.db and loaded sample data.")
    conn.close()


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
    """Monthly attendance report: for each subject, lectures held this month vs. lectures this student attended."""
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
    session.pop("faculty_code", None)
    return jsonify(ok=True)


@app.route("/api/faculty/password", methods=["POST"])
def faculty_change_password():
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    body = request.get_json(silent=True) or {}
    current = str(body.get("current", ""))
    new = str(body.get("new", ""))
    if len(new) < 4:
        return jsonify(error="New password should be at least 4 characters."), 400
    conn = get_conn()
    try:
        row = conn.execute("SELECT password_hash FROM teacher_auth WHERE code=?", (code,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current):
            return jsonify(error="Current password is incorrect."), 401
        conn.execute("UPDATE teacher_auth SET password_hash=? WHERE code=?", (generate_password_hash(new), code))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)


@app.route("/api/faculty/schedule")
def faculty_schedule():
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT t.id, t.day, t.start_period, t.end_period, t.batch, t.subject_code AS code, "
            "  sub.name AS subject, b.name AS branch, b.id AS branch_id, "
            "  COALESCE(t.room, b.default_room) AS room, p1.start_time AS start, p2.end_time AS end "
            "FROM timetable t "
            "JOIN branches b ON b.id = t.branch_id "
            "JOIN subjects sub ON sub.code = t.subject_code "
            "JOIN periods p1 ON p1.no = t.start_period "
            "JOIN periods p2 ON p2.no = t.end_period "
            "WHERE t.faculty_code = ? "
            f"ORDER BY {DAY_ORDER}, t.start_period",
            (code,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify(schedule=[dict(r) for r in rows])


@app.route("/api/faculty/options")
def faculty_options():
    """Dropdown data for the timetable-edit form."""
    conn = get_conn()
    try:
        subjects = [dict(r) for r in conn.execute("SELECT code, name FROM subjects ORDER BY name")]
        periods = [dict(r) for r in conn.execute("SELECT no, start_time, end_time FROM periods ORDER BY no")]
    finally:
        conn.close()
    return jsonify(subjects=subjects, periods=periods, days=["Mon", "Tue", "Wed", "Thu", "Fri"])


def _own_slot(conn, code, timetable_id):
    return conn.execute(
        "SELECT * FROM timetable WHERE id=? AND faculty_code=?", (timetable_id, code)
    ).fetchone()


@app.route("/api/faculty/class/<int:timetable_id>")
def faculty_class(timetable_id):
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        slot = _own_slot(conn, code, timetable_id)
        if not slot:
            return jsonify(error="That class is not on your timetable."), 404

        students = conn.execute(
            "SELECT roll_no, name, sr_no FROM students WHERE branch_id=? AND (? IS NULL OR batch=?) ORDER BY sr_no",
            (slot["branch_id"], slot["batch"], slot["batch"]),
        ).fetchall()
        marked = {r["roll_no"]: r["status"] for r in conn.execute(
            "SELECT roll_no, status FROM attendance WHERE timetable_id=? AND date=?", (timetable_id, date)
        )}
    finally:
        conn.close()
    roster = [{"roll_no": s["roll_no"], "name": s["name"], "sr_no": s["sr_no"],
               "status": marked.get(s["roll_no"], "Present")} for s in students]
    return jsonify(date=date, batch=slot["batch"], roster=roster, already_marked=bool(marked))


@app.route("/api/faculty/attendance", methods=["POST"])
def save_attendance():
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    body = request.get_json(silent=True) or {}
    timetable_id = body.get("timetable_id")
    date = body.get("date") or datetime.now().strftime("%Y-%m-%d")
    records = body.get("records") or []

    conn = get_conn()
    try:
        slot = _own_slot(conn, code, timetable_id)
        if not slot:
            return jsonify(error="That class is not on your timetable."), 404

        valid_rolls = {r["roll_no"] for r in conn.execute(
            "SELECT roll_no FROM students WHERE branch_id=? AND (? IS NULL OR batch=?)",
            (slot["branch_id"], slot["batch"], slot["batch"]),
        )}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        present = 0
        saved = 0
        for rec in records:
            roll_no = rec.get("roll_no")
            status = rec.get("status")
            if roll_no not in valid_rolls or status not in ("Present", "Absent"):
                continue  # skip anything that doesn't belong to this class
            conn.execute(
                "INSERT INTO attendance (timetable_id, date, roll_no, status, marked_by, marked_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(timetable_id, date, roll_no) DO UPDATE SET status=excluded.status, marked_by=excluded.marked_by, marked_at=excluded.marked_at",
                (timetable_id, date, roll_no, status, code, now),
            )
            saved += 1
            if status == "Present":
                present += 1
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, saved_at=now, total=saved, present=present)


@app.route("/api/faculty/attendance/history/<int:timetable_id>")
def attendance_history(timetable_id):
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    conn = get_conn()
    try:
        if not _own_slot(conn, code, timetable_id):
            return jsonify(error="That class is not on your timetable."), 404
        rows = conn.execute(
            "SELECT date, COUNT(*) AS total, SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) AS present, "
            "MAX(marked_at) AS marked_at "
            "FROM attendance WHERE timetable_id=? GROUP BY date ORDER BY date DESC",
            (timetable_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify(history=[dict(r) for r in rows])


@app.route("/api/faculty/timetable/<int:timetable_id>", methods=["PATCH"])
def edit_timetable(timetable_id):
    code = require_login()
    if not code:
        return jsonify(error="Please log in again."), 401
    body = request.get_json(silent=True) or {}
    conn = get_conn()
    try:
        slot = _own_slot(conn, code, timetable_id)
        if not slot:
            return jsonify(error="That class is not on your timetable."), 404

        day = body.get("day", slot["day"])
        start_period = int(body.get("start_period", slot["start_period"]))
        end_period = int(body.get("end_period", slot["end_period"]))
        room = (body.get("room") or "").strip() or None
        subject_code = body.get("subject_code", slot["subject_code"])

        if day not in ("Mon", "Tue", "Wed", "Thu", "Fri"):
            return jsonify(error="Not a valid day."), 400
        if end_period < start_period:
            return jsonify(error="End period can't be before the start period."), 400
        if not conn.execute("SELECT 1 FROM subjects WHERE code=?", (subject_code,)).fetchone():
            return jsonify(error="Not a valid subject."), 400

        conn.execute(
            "UPDATE timetable SET day=?, start_period=?, end_period=?, room=?, subject_code=? WHERE id=?",
            (day, start_period, end_period, room, subject_code, timetable_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
