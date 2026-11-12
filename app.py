# -*- coding: utf-8 -*-
"""
UPAMS - Undergraduate Project Allocation and Management System
Federal Polytechnic Nasarawa - Department of Computer Science (Software Engineering)

A standards-compliant final year project that fully digitises the workflow:
    Admin     -> creates sessions, registers users, allocates supervisors,
                 sets quotas, monitors everything, generates printable reports.
    Supervisor-> approves/rejects topics, requests corrections per chapter,
                 grades defences, sees student progress, generates reports.
    Student   -> submits topic, uploads chapter drafts, views corrections,
                 tracks progress, sees final score.

Python 3.7+ compatible. No walrus operators, no match/case, no `X | Y` types.
"""

from __future__ import annotations

import os
import io
import csv
import re
import uuid
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, send_file, abort, jsonify, Response, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# --------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------- #
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

DB_PATH = os.path.join(INSTANCE_DIR, 'upams.db')
ALLOWED_EXTS = {'pdf', 'doc', 'docx', 'txt', 'odt'}
MAX_UPLOAD_MB = 25

app = Flask(__name__)
app.config['SECRET_KEY'] = 'fpn-upams-2026-' + uuid.uuid4().hex[:12]
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
# Keep users logged in for 30 days so the system doesn't auto-logout during defense.
# Session only ends when the user explicitly clicks Logout.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# --------------------------------------------------------------------- #
# Database layer (raw sqlite3 - works on any Python 3.7+)
# --------------------------------------------------------------------- #
def get_db():
    """Return a per-request sqlite3 connection with Row factory."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_db(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last = cur.lastrowid
    cur.close()
    return last


# --------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    UNIQUE NOT NULL,
    password_hash   TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK(role IN ('admin','supervisor','student')),
    full_name       TEXT    NOT NULL,
    email           TEXT,
    phone           TEXT,
    gender          TEXT,                          -- Male / Female
    title           TEXT,                          -- Mr/Mrs/Dr/Engr etc.
    matric_number   TEXT,                          -- students: registration number
    staff_id        TEXT,                          -- supervisors: staff ID
    department      TEXT,
    level           TEXT,                          -- ND2 / HND2
    specialization  TEXT,                          -- Software Engineering / Networking & Cloud
    is_active       INTEGER DEFAULT 1,
    force_pwd_change INTEGER DEFAULT 0,             -- forced after admin reset
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login      TIMESTAMP
);

-- Pre-loaded identity records (admin uploads CSV; students claim accounts) --
CREATE TABLE IF NOT EXISTS preload_students (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_number TEXT UNIQUE NOT NULL,
    full_name           TEXT NOT NULL,
    email               TEXT,
    phone               TEXT,
    gender              TEXT,
    department          TEXT,
    level               TEXT,                       -- ND2 / HND2
    specialization      TEXT,
    claimed             INTEGER DEFAULT 0,          -- 1 once user account created
    claimed_user_id     INTEGER,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS preload_supervisors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id        TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL,
    title           TEXT,
    email           TEXT,
    phone           TEXT,
    gender          TEXT,
    department      TEXT,
    claimed         INTEGER DEFAULT 0,
    claimed_user_id INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT    UNIQUE NOT NULL,       -- e.g. 2025/2026
    semester        TEXT    NOT NULL,              -- First / Second
    start_date      DATE,
    end_date        DATE,
    is_current      INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    supervisor_id   INTEGER NOT NULL,
    session_id      INTEGER NOT NULL,
    allocated_by    INTEGER NOT NULL,
    allocated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'active',         -- active / reassigned / completed
    UNIQUE(student_id, session_id),
    FOREIGN KEY(student_id)    REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(supervisor_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(session_id)    REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(allocated_by)  REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    supervisor_id   INTEGER NOT NULL,
    session_id      INTEGER NOT NULL,
    title           TEXT    NOT NULL,
    background      TEXT,
    objectives      TEXT,
    methodology     TEXT,
    submitted_by    TEXT NOT NULL,                 -- 'student' or 'supervisor'
    status          TEXT DEFAULT 'pending',        -- pending / approved / rejected
    supervisor_note TEXT,
    decided_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(student_id)    REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(supervisor_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(session_id)    REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chapters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL,
    student_id      INTEGER NOT NULL,
    supervisor_id   INTEGER NOT NULL,
    chapter_no      INTEGER NOT NULL CHECK(chapter_no BETWEEN 1 AND 5),
    title           TEXT,
    file_path       TEXT,                          -- relative to UPLOAD_DIR
    original_name   TEXT,
    notes           TEXT,                          -- student's submission note
    status          TEXT DEFAULT 'submitted',      -- submitted / approved / corrections / rejected
    correction_note TEXT,
    grade           INTEGER,                       -- 0-100
    version         INTEGER DEFAULT 1,
    submitted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     TIMESTAMP,
    FOREIGN KEY(topic_id)      REFERENCES topics(id) ON DELETE CASCADE,
    FOREIGN KEY(student_id)    REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(supervisor_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS defences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER UNIQUE NOT NULL,
    student_id      INTEGER NOT NULL,
    supervisor_id   INTEGER NOT NULL,
    scheduled_date  DATE,
    venue           TEXT,
    presentation_score INTEGER,                    -- 0-30
    project_score      INTEGER,                    -- 0-40
    chapters_score     INTEGER,                    -- 0-30  (from chapter avg)
    total_score        INTEGER,                    -- 0-100
    grade           TEXT,                          -- A B C D F
    panel_remarks   TEXT,
    status          TEXT DEFAULT 'scheduled',      -- scheduled / completed / postponed
    completed_at    TIMESTAMP,
    FOREIGN KEY(topic_id)      REFERENCES topics(id) ON DELETE CASCADE,
    FOREIGN KEY(student_id)    REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(supervisor_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER,
    chapter_id      INTEGER,
    from_user_id    INTEGER NOT NULL,
    to_user_id      INTEGER NOT NULL,
    body            TEXT NOT NULL,
    is_read         INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(from_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(to_user_id)   REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT,
    link            TEXT,
    icon            TEXT,
    is_read         INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    action          TEXT NOT NULL,
    target          TEXT,
    details         TEXT,
    ip_address      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    audience        TEXT DEFAULT 'all',            -- all / students / supervisors
    created_by      INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_student ON topics(student_id);
CREATE INDEX IF NOT EXISTS idx_chapters_topic ON chapters(topic_id);
CREATE INDEX IF NOT EXISTS idx_notif_user     ON notifications(user_id, is_read);
"""


def init_db():
    """Create the schema and seed default data."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    if cur.fetchone()[0] == 0:
        # ============ ADMIN ============
        conn.execute(
            "INSERT INTO users (username,password_hash,role,full_name,email,title,department) "
            "VALUES (?,?,?,?,?,?,?)",
            ('admin', generate_password_hash('admin123'), 'admin',
             'System Administrator', 'admin@fpn.edu.ng', 'Mr',
             'Computer Science')
        )

        # ============ SUPERVISORS (preload records) ============
        preload_sups = [
            ('FPN/STF/CS/001', 'Mebawondu Joseph O.',  'Dr',   'jmebawondu@fpn.edu.ng', '08031000001', 'Male',   'Computer Science'),
            ('FPN/STF/CS/002', 'Ibrahim Abdullahi',    'Engr', 'aibrahim@fpn.edu.ng',   '08031000002', 'Male',   'Computer Science'),
            ('FPN/STF/CS/003', 'Adebayo Folake',       'Mrs',  'fadebayo@fpn.edu.ng',   '08031000003', 'Female', 'Computer Science'),
            ('FPN/STF/CS/004', 'Onoja Kingsley',       'Mr',   'konoja@fpn.edu.ng',     '08031000004', 'Male',   'Computer Science'),
            ('FPN/STF/CS/005', 'Yakubu Hauwa',         'Mrs',  'yhauwa@fpn.edu.ng',     '08031000005', 'Female', 'Computer Science'),
            ('FPN/STF/CS/006', 'Okeke Chukwuma',       'Dr',   'cokeke@fpn.edu.ng',     '08031000006', 'Male',   'Computer Science'),
        ]
        # Pre-claimed usernames (only these two get a real password and can log in)
        claimed_map = {
            'FPN/STF/CS/001': ('jmebawondu', generate_password_hash('super123')),
            'FPN/STF/CS/002': ('aibrahim',   generate_password_hash('super123')),
        }
        for sid, fn, ti, em, ph, gn, dept in preload_sups:
            # 1) Insert preload record
            conn.execute(
                "INSERT INTO preload_supervisors (staff_id,full_name,title,email,phone,gender,department) "
                "VALUES (?,?,?,?,?,?,?)",
                (sid, fn, ti, em, ph, gn, dept)
            )
            # 2) Insert matching user (activated if pre-claimed, shell otherwise)
            if sid in claimed_map:
                uname, pwd_hash = claimed_map[sid]
                cur2 = conn.execute(
                    "INSERT INTO users (username,password_hash,role,full_name,email,title,phone,gender,staff_id,department,is_active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (uname, pwd_hash, 'supervisor', fn, em, ti, ph, gn, sid, dept, 1)
                )
                conn.execute(
                    "UPDATE preload_supervisors SET claimed=1, claimed_user_id=? WHERE staff_id=?",
                    (cur2.lastrowid, sid)
                )
            else:
                # Shell account: cannot log in until they self-register and set a password.
                shell_uname = sid.lower().replace('/', '_')
                shell_pwd = generate_password_hash('!unset!' + uuid.uuid4().hex)
                conn.execute(
                    "INSERT INTO users (username,password_hash,role,full_name,email,title,phone,gender,staff_id,department,is_active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (shell_uname, shell_pwd, 'supervisor', fn, em, ti, ph, gn, sid, dept, 0)
                )

        # ============ STUDENTS (preload records) ============
        # IMPORTANT: This is now minimal seed data. The real student data should
        # be uploaded via the CSV import feature. We only keep ONE sample
        # pre-loaded student per level so the demo works out of the box.
        # All other students are expected to come from CSV uploads.
        seed_students = [
            ('FPN/S05/2024/2025/CS/SW/001', 'Saviour Adamu',   'saviour.adamu@stu.fpn.edu.ng',   '08031002001', 'Male',   'HND2', 'Software Engineering'),
            ('FPN/S05/2024/2025/CS/NC/001', 'Promise Atuba',   'promise.atuba@stu.fpn.edu.ng',   '08032002001', 'Female', 'HND2', 'Networking & Cloud Computing'),
            ('FPN/S05/2024/2025/CS/001',    'Friday Tanko',    'friday.tanko@stu.fpn.edu.ng',    '08033002001', 'Male',   'ND2',  None),
        ]
        for reg, fn, em, ph, gn, lv, spec in seed_students:
            # 1) Preload identity record
            conn.execute(
                "INSERT INTO preload_students (registration_number,full_name,email,phone,gender,"
                "department,level,specialization) VALUES (?,?,?,?,?,?,?,?)",
                (reg, fn, em, ph, gn, 'Computer Science', lv, spec)
            )
            # 2) Shell user account (inactive until student self-registers and sets password)
            shell_uname = reg.lower().replace('/', '_')
            shell_pwd = generate_password_hash('!unset!' + uuid.uuid4().hex)
            conn.execute(
                "INSERT INTO users (username,password_hash,role,full_name,email,phone,gender,"
                "matric_number,department,level,specialization,is_active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (shell_uname, shell_pwd, 'student', fn, em, ph, gn,
                 reg, 'Computer Science', lv, spec, 0)
            )
        # NOTE: We deliberately do NOT auto-claim any student here.
        # All seeded students are UNCLAIMED (shell accounts) so they can be
        # registered via the self-registration flow (just like CSV-imported students).
        claimed_student_ids = []  # kept for backwards compatibility below

        # ============ CURRENT SESSION ============
        conn.execute(
            "INSERT INTO sessions (session_name,semester,start_date,end_date,is_current) "
            "VALUES (?,?,?,?,1)",
            ('2024/2025', 'Second',
             datetime.now().date().isoformat(),
             (datetime.now() + timedelta(days=180)).date().isoformat())
        )

        # Welcome announcement
        conn.execute(
            "INSERT INTO announcements (title,body,audience,created_by) VALUES (?,?,?,?)",
            ('Welcome to UPAMS',
             'The Undergraduate Project Allocation and Management System is live. '
             'Students and supervisors should self-register using their registration '
             'number / staff ID. Default password rules apply after admin resets.',
             'all', 1)
        )

        # Demo allocation + topic for first claimed student
        sess_id = conn.execute("SELECT id FROM sessions WHERE is_current=1").fetchone()[0]
        sup_ids = [r[0] for r in conn.execute(
            "SELECT id FROM users WHERE role='supervisor' ORDER BY id").fetchall()]
        if sup_ids and claimed_student_ids:
            first_sid = claimed_student_ids[0]
            first_supid = sup_ids[0]
            conn.execute(
                "INSERT INTO allocations (student_id,supervisor_id,session_id,allocated_by) "
                "VALUES (?,?,?,?)",
                (first_sid, first_supid, sess_id, 1))
            conn.execute(
                "INSERT INTO notifications (user_id,title,body,link) VALUES (?,?,?,?)",
                (first_sid, 'Supervisor Allocated',
                 'You have been allocated a project supervisor for this session.',
                 '/student/topic'))
            conn.execute(
                "INSERT INTO topics (student_id,supervisor_id,session_id,title,background,"
                "objectives,methodology,status,submitted_by,supervisor_note,decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (first_sid, first_supid, sess_id,
                 'Design and Implementation of an Undergraduate Project Allocation and '
                 'Management System (A Case Study of The Federal Polytechnic, Nasarawa)',
                 'Project administration at FPN is currently paper-based. Topics get lost, '
                 'allocation is opaque, chapter feedback is informal.',
                 'i. To design a centralised platform for project workflow.\n'
                 'ii. To implement supervisor allocation and chapter review.\n'
                 'iii. To evaluate the platform with real users at FPN.',
                 'Iterative web development using Flask, SQLite, and Jinja2; deployed on a '
                 'local server with HTTPS. Evaluated through user testing.',
                 'approved', 'student',
                 'Excellent scoping. Proceed with implementation.',
                 datetime.now().isoformat()))

    conn.commit()
    conn.close()


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)


@app.context_processor
def inject_globals():
    user = current_user()
    notif_count = 0
    cur_session_row = None
    if user:
        row = query_db(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
            (user['id'],), one=True
        )
        notif_count = row['c'] if row else 0
        cur_session_row = query_db(
            "SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True
        )
    return dict(
        current_user=user,
        notif_count=notif_count,
        current_session=cur_session_row,
        now=datetime.now(),
        APP_NAME='UPAMS',
        SCHOOL_NAME='Federal Polytechnic, Nasarawa',
    )


def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash('Please log in to continue.', 'warning')
                return redirect(url_for('login', next=request.path))
            if role:
                roles = role if isinstance(role, (list, tuple)) else [role]
                if user['role'] not in roles:
                    flash('You are not permitted to access that area.', 'danger')
                    return redirect(url_for('dashboard'))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def log_action(action, target='', details=''):
    user = current_user()
    uid = user['id'] if user else None
    execute_db(
        "INSERT INTO audit_log (user_id,action,target,details,ip_address) VALUES (?,?,?,?,?)",
        (uid, action, target, details, request.remote_addr or '')
    )


def notify(user_id, title, body='', link='', icon='bell'):
    execute_db(
        "INSERT INTO notifications (user_id,title,body,link,icon) VALUES (?,?,?,?,?)",
        (user_id, title, body, link, icon)
    )


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS


def grade_from_score(score):
    if score is None:
        return '-'
    if score >= 70: return 'A'
    if score >= 60: return 'B'
    if score >= 50: return 'C'
    if score >= 40: return 'D'
    return 'F'


def normalize_topic_title(title):
    """Normalise a topic title for duplicate detection.

    Lower-cases, strips, and collapses every run of non-alphanumeric characters
    (punctuation, slashes, extra spaces, line breaks, etc.) into a single space.
    This means "Design & Implementation of X!" and
    "design and implementation of  x" are treated as the SAME topic, so students
    or supervisors cannot bypass the check with cosmetic changes.
    """
    if not title:
        return ''
    t = title.strip().lower()
    t = t.replace('&', ' and ')
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def find_topic_conflict(title, session_id, exclude_student_id=None,
                        statuses=('pending', 'approved')):
    """Return the first clashing topic in the session, or None if the title is free.

    A clash is any topic belonging to a DIFFERENT student whose normalised title
    matches `title` and whose status is in `statuses` (by default any pending or
    approved topic). Rejected topics are ignored, so a freed-up title can be
    reused later. The returned row carries the other student's and supervisor's
    names so callers can build a clear, specific message.
    """
    norm = normalize_topic_title(title)
    if not norm:
        return None
    placeholders = ','.join(['?'] * len(statuses))
    rows = query_db(
        "SELECT t.*, "
        "stu.full_name AS conflict_student_name, "
        "stu.matric_number AS conflict_student_matric, "
        "sup.full_name AS conflict_supervisor_name, "
        "sup.title AS conflict_supervisor_title "
        "FROM topics t "
        "JOIN users stu ON stu.id = t.student_id "
        "LEFT JOIN users sup ON sup.id = t.supervisor_id "
        "WHERE t.session_id = ? AND t.status IN (" + placeholders + ")",
        tuple([session_id] + list(statuses))
    )
    for r in rows:
        if exclude_student_id and r['student_id'] == exclude_student_id:
            continue
        if normalize_topic_title(r['title']) == norm:
            return r
    return None


# --------------------------------------------------------------------- #
# Public / Auth routes
# --------------------------------------------------------------------- #
@app.route('/')
def home():
    if current_user():
        return redirect(url_for('dashboard'))
    stats = {
        'students':    query_db("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)['c'],
        'supervisors': query_db("SELECT COUNT(*) c FROM users WHERE role='supervisor'", one=True)['c'],
        'topics':      query_db("SELECT COUNT(*) c FROM topics", one=True)['c'],
        'completed':   query_db("SELECT COUNT(*) c FROM defences WHERE status='completed'", one=True)['c'],
    }
    return render_template('home.html', stats=stats)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u_raw = request.form.get('username', '').strip()
        u = u_raw.lower()
        p = request.form.get('password', '')
        # Try by username, then by matric_number, then by staff_id (case-insensitive)
        row = query_db(
            "SELECT * FROM users WHERE lower(username)=? OR lower(matric_number)=? OR lower(staff_id)=?",
            (u, u_raw.lower(), u_raw.lower()), one=True)
        if row and check_password_hash(row['password_hash'], p) and row['is_active']:
            session.clear()
            session.permanent = True  # Honour the 30-day PERMANENT_SESSION_LIFETIME
            session['user_id'] = row['id']
            session['role'] = row['role']
            execute_db("UPDATE users SET last_login=? WHERE id=?",
                       (datetime.now(), row['id']))
            log_action('login', 'auth', 'Successful login')
            flash('Welcome back, ' + row['full_name'] + '!', 'success')
            if row['force_pwd_change']:
                flash('Your password was reset by the administrator. Please change it now.', 'warning')
                return redirect(url_for('profile'))
            nxt = request.args.get('next') or url_for('dashboard')
            return redirect(nxt)
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


# --------------------------------------------------------------------- #
# Self-Registration (pre-load gated)
# --------------------------------------------------------------------- #
@app.route('/register')
def register_choice():
    """Choose Student or Supervisor registration."""
    return render_template('register_choice.html')


@app.route('/register/student', methods=['GET', 'POST'])
def register_student():
    """
    Two-step:
      Step 1 (GET or POST without password): user enters reg number; we look it up.
      Step 2 (POST with password): activate the shell account by setting the password.
    """
    lookup = None
    reg_input = request.args.get('reg', '').strip().upper()
    if request.method == 'POST':
        reg_input = request.form.get('registration_number', '').strip().upper()

    if reg_input:
        lookup = query_db(
            "SELECT * FROM preload_students WHERE upper(registration_number)=?",
            (reg_input,), one=True)
        if not lookup:
            flash('Registration number not found. Please contact the department '
                  'administrator to be added to the system.', 'danger')
            return render_template('register_student.html', lookup=None, reg_input=reg_input)
        # claimed=1 means the student has already activated their account
        # (set their own password and logged in). Only THEN do we block.
        if lookup['claimed']:
            flash('An account has already been activated for this registration number. '
                  'If you forgot your password, contact the administrator for a reset.',
                  'warning')
            return render_template('register_student.html', lookup=None, reg_input=reg_input)
        # If only the level is acceptable (ND2 / HND2). Reg numbers in the seed are
        # all valid levels, but defend anyway:
        if lookup['level'] not in ('ND2', 'HND2'):
            flash('Only ND2 and HND2 students can register on this system.', 'danger')
            return render_template('register_student.html', lookup=None, reg_input=reg_input)

    # Step 2: complete registration if password provided
    if (request.method == 'POST' and lookup
            and request.form.get('password')):
        pwd  = request.form.get('password', '')
        pwd2 = request.form.get('password_confirm', '')
        if len(pwd) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('register_student.html', lookup=lookup, reg_input=reg_input)
        if pwd != pwd2:
            flash('Passwords do not match.', 'danger')
            return render_template('register_student.html', lookup=lookup, reg_input=reg_input)

        # Find the shell user that was auto-created when this record was
        # pre-loaded (either via CSV upload or initial seed). If it exists,
        # activate it. If somehow it doesn't (legacy data), create a fresh one.
        shell = query_db(
            "SELECT * FROM users WHERE matric_number=? AND role='student'",
            (lookup['registration_number'],), one=True)
        if shell:
            # Activate the existing shell: set real password, activate the account.
            execute_db(
                "UPDATE users SET password_hash=?, is_active=1, force_pwd_change=0 "
                "WHERE id=?",
                (generate_password_hash(pwd), shell['id']))
            new_uid = shell['id']
        else:
            # Legacy fallback: no shell exists, create a fresh account.
            uname = lookup['registration_number'].lower().replace('/', '_')
            new_uid = execute_db(
                "INSERT INTO users (username,password_hash,role,full_name,email,phone,gender,"
                "matric_number,department,level,specialization) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uname, generate_password_hash(pwd), 'student',
                 lookup['full_name'], lookup['email'], lookup['phone'], lookup['gender'],
                 lookup['registration_number'], lookup['department'], lookup['level'],
                 lookup['specialization']))
        execute_db(
            "UPDATE preload_students SET claimed=1, claimed_user_id=? WHERE id=?",
            (new_uid, lookup['id']))
        # Audit
        execute_db(
            "INSERT INTO audit_log (user_id,action,target,details) VALUES (?,?,?,?)",
            (new_uid, 'self_register', 'student', lookup['registration_number']))
        # Notify admin
        admins = query_db("SELECT id FROM users WHERE role='admin'")
        for a in admins:
            execute_db(
                "INSERT INTO notifications (user_id,title,body,link) VALUES (?,?,?,?)",
                (a['id'], 'New Student Registered',
                 lookup['full_name'] + ' (' + lookup['registration_number'] + ') '
                 'has just registered.', '/admin/users?role=student'))
        flash('Registration successful! You can now sign in with your registration '
              'number or username and the password you chose.', 'success')
        return redirect(url_for('login'))

    return render_template('register_student.html', lookup=lookup, reg_input=reg_input)


@app.route('/register/supervisor', methods=['GET', 'POST'])
def register_supervisor():
    """Mirror of register_student but for staff_id."""
    lookup = None
    sid_input = request.args.get('sid', '').strip().upper()
    if request.method == 'POST':
        sid_input = request.form.get('staff_id', '').strip().upper()

    if sid_input:
        lookup = query_db(
            "SELECT * FROM preload_supervisors WHERE upper(staff_id)=?",
            (sid_input,), one=True)
        if not lookup:
            flash('Staff ID not found. Please contact the administrator to be '
                  'added to the system.', 'danger')
            return render_template('register_supervisor.html', lookup=None, sid_input=sid_input)
        if lookup['claimed']:
            flash('An account has already been activated for this staff ID. Contact the '
                  'administrator for a password reset if needed.', 'warning')
            return render_template('register_supervisor.html', lookup=None, sid_input=sid_input)

    if (request.method == 'POST' and lookup
            and request.form.get('password')):
        pwd  = request.form.get('password', '')
        pwd2 = request.form.get('password_confirm', '')
        if len(pwd) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('register_supervisor.html', lookup=lookup, sid_input=sid_input)
        if pwd != pwd2:
            flash('Passwords do not match.', 'danger')
            return render_template('register_supervisor.html', lookup=lookup, sid_input=sid_input)

        # Find the shell account created during preload, and activate it.
        shell = query_db(
            "SELECT * FROM users WHERE staff_id=? AND role='supervisor'",
            (lookup['staff_id'],), one=True)
        if shell:
            execute_db(
                "UPDATE users SET password_hash=?, is_active=1, force_pwd_change=0 "
                "WHERE id=?",
                (generate_password_hash(pwd), shell['id']))
            new_uid = shell['id']
        else:
            # Legacy fallback: create a fresh account if no shell exists
            parts = lookup['staff_id'].split('/')
            base = parts[-1] if parts else lookup['staff_id']
            uname = (lookup['full_name'].split()[0] + base).lower()
            uname = ''.join(c if c.isalnum() else '' for c in uname)
            if query_db("SELECT 1 FROM users WHERE username=?", (uname,), one=True):
                uname = uname + str(int(datetime.now().timestamp()))[-4:]
            new_uid = execute_db(
                "INSERT INTO users (username,password_hash,role,full_name,email,phone,gender,title,"
                "staff_id,department) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uname, generate_password_hash(pwd), 'supervisor',
                 lookup['full_name'], lookup['email'], lookup['phone'], lookup['gender'],
                 lookup['title'], lookup['staff_id'], lookup['department']))
        execute_db(
            "UPDATE preload_supervisors SET claimed=1, claimed_user_id=? WHERE id=?",
            (new_uid, lookup['id']))
        execute_db(
            "INSERT INTO audit_log (user_id,action,target,details) VALUES (?,?,?,?)",
            (new_uid, 'self_register', 'supervisor', lookup['staff_id']))
        admins = query_db("SELECT id FROM users WHERE role='admin'")
        for a in admins:
            execute_db(
                "INSERT INTO notifications (user_id,title,body,link) VALUES (?,?,?,?)",
                (a['id'], 'New Supervisor Registered',
                 (lookup['title'] or '') + ' ' + lookup['full_name'] + ' ('
                 + lookup['staff_id'] + ') has just registered.',
                 '/admin/users?role=supervisor'))
        flash('Registration successful! You can now sign in.', 'success')
        return redirect(url_for('login'))

    return render_template('register_supervisor.html', lookup=lookup, sid_input=sid_input)


@app.route('/logout')
def logout():
    log_action('logout', 'auth', '')
    session.clear()
    flash('You have been signed out.', 'info')
    return redirect(url_for('home'))


# --------------------------------------------------------------------- #
# Dashboard router
# --------------------------------------------------------------------- #
@app.route('/dashboard')
@login_required()
def dashboard():
    user = current_user()
    if user['role'] == 'admin':
        return admin_dashboard()
    if user['role'] == 'supervisor':
        return supervisor_dashboard()
    return student_dashboard()


# --------------------------------------------------------------------- #
# ADMIN  =============================================================== #
# --------------------------------------------------------------------- #
def admin_dashboard():
    stats = {
        'students':    query_db("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)['c'],
        'supervisors': query_db("SELECT COUNT(*) c FROM users WHERE role='supervisor'", one=True)['c'],
        'topics':      query_db("SELECT COUNT(*) c FROM topics", one=True)['c'],
        'approved':    query_db("SELECT COUNT(*) c FROM topics WHERE status='approved'", one=True)['c'],
        'pending':     query_db("SELECT COUNT(*) c FROM topics WHERE status='pending'", one=True)['c'],
        'chapters':    query_db("SELECT COUNT(*) c FROM chapters", one=True)['c'],
        'completed':   query_db("SELECT COUNT(*) c FROM defences WHERE status='completed'", one=True)['c'],
        'unalloc':     query_db("""SELECT COUNT(*) c FROM users u
                                    WHERE role='student' AND NOT EXISTS
                                    (SELECT 1 FROM allocations a WHERE a.student_id=u.id
                                     AND a.status='active')""", one=True)['c'],
    }
    recent_topics = query_db("""SELECT t.*, s.full_name AS student_name, sv.full_name AS sup_name
                                FROM topics t
                                JOIN users s  ON s.id  = t.student_id
                                JOIN users sv ON sv.id = t.supervisor_id
                                ORDER BY t.created_at DESC LIMIT 6""")
    recent_chapters = query_db("""SELECT c.*, s.full_name AS student_name, t.title AS topic_title
                                  FROM chapters c
                                  JOIN users s ON s.id = c.student_id
                                  JOIN topics t ON t.id = c.topic_id
                                  ORDER BY c.submitted_at DESC LIMIT 6""")
    activity = query_db("""SELECT a.*, u.full_name FROM audit_log a
                           LEFT JOIN users u ON u.id = a.user_id
                           ORDER BY a.created_at DESC LIMIT 8""")
    sup_load = query_db("""SELECT sv.id, sv.full_name, sv.title,
                                  COUNT(a.id) AS load
                           FROM users sv
                           LEFT JOIN allocations a
                                  ON a.supervisor_id=sv.id AND a.status='active'
                           WHERE sv.role='supervisor' AND sv.is_active=1
                           GROUP BY sv.id ORDER BY load DESC LIMIT 6""")
    return render_template('admin/dashboard.html',
                           stats=stats, recent_topics=recent_topics,
                           recent_chapters=recent_chapters,
                           activity=activity, sup_load=sup_load)


# ----- Users management ---------------------------------------------- #
@app.route('/admin/users')
@login_required('admin')
def admin_users():
    role = request.args.get('role', 'all')
    q = request.args.get('q', '').strip()
    sql = "SELECT * FROM users WHERE 1=1"
    args = []
    if role != 'all':
        sql += " AND role=?"
        args.append(role)
    if q:
        sql += " AND (full_name LIKE ? OR username LIKE ? OR matric_number LIKE ? OR email LIKE ?)"
        like = '%' + q + '%'
        args += [like, like, like, like]
    sql += " ORDER BY role, full_name"
    users = query_db(sql, args)
    return render_template('admin/users.html', users=users, role=role, q=q)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@login_required('admin')
def admin_user_new():
    if request.method == 'POST':
        f = request.form
        username = f.get('username', '').strip().lower()
        role = f.get('role', '').strip()
        if not username or not role:
            flash('Username and role are required.', 'danger')
            return render_template('admin/user_form.html', user=None)
        if query_db("SELECT 1 FROM users WHERE lower(username)=?", (username,), one=True):
            flash('Username already exists.', 'danger')
            return render_template('admin/user_form.html', user=None)
        level = (f.get('level') or '').strip().upper() or None
        if role == 'student' and level not in ('ND2', 'HND2'):
            flash('Students must be ND2 or HND2 (those are the only project-doing levels).', 'danger')
            return render_template('admin/user_form.html', user=None)
        pwd = f.get('password') or 'changeme123'
        execute_db(
            """INSERT INTO users (username,password_hash,role,full_name,email,phone,gender,title,
                                  matric_number,staff_id,department,level,specialization)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (username, generate_password_hash(pwd), role,
             f.get('full_name', '').strip(), f.get('email', '').strip(),
             f.get('phone', '').strip(),
             (f.get('gender') or '').strip().title() or None,
             f.get('title', '').strip(),
             f.get('matric_number', '').strip() or None,
             f.get('staff_id', '').strip() or None,
             f.get('department', 'Computer Science').strip(),
             level,
             f.get('specialization', '').strip() or None)
        )
        log_action('create_user', 'user:' + username, role)
        flash('User created. Default password: ' + pwd, 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=None)


@app.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required('admin')
def admin_user_edit(uid):
    user = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not user:
        abort(404)
    if request.method == 'POST':
        f = request.form
        level = (f.get('level') or '').strip().upper() or None
        if user['role'] == 'student' and level and level not in ('ND2', 'HND2'):
            flash('Students must be ND2 or HND2.', 'danger')
            return render_template('admin/user_form.html', user=user)
        execute_db(
            """UPDATE users SET full_name=?, email=?, phone=?, gender=?, title=?,
                                matric_number=?, staff_id=?, department=?, level=?,
                                specialization=?, is_active=?
               WHERE id=?""",
            (f.get('full_name'), f.get('email'), f.get('phone'),
             (f.get('gender') or '').strip().title() or None,
             f.get('title'),
             f.get('matric_number') or None,
             f.get('staff_id') or None,
             f.get('department'),
             level, f.get('specialization') or None,
             1 if f.get('is_active') == 'on' else 0, uid)
        )
        if f.get('password'):
            execute_db("UPDATE users SET password_hash=?, force_pwd_change=0 WHERE id=?",
                       (generate_password_hash(f.get('password')), uid))
        log_action('edit_user', 'user:' + user['username'], '')
        flash('User updated.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=user)


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@login_required('admin')
def admin_user_toggle(uid):
    user = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not user:
        abort(404)
    execute_db("UPDATE users SET is_active=? WHERE id=?",
               (0 if user['is_active'] else 1, uid))
    log_action('toggle_user', 'user:' + user['username'], '')
    flash('User status updated.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/reset-password', methods=['POST'])
@login_required('admin')
def admin_user_reset_password(uid):
    """Reset to a default. Students -> matric_number (slugified). Supervisors -> staff_id.
       Admins must use 'admin123'. Custom temp pwd supported via 'custom_pwd' field."""
    user = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not user:
        abort(404)
    custom = (request.form.get('custom_pwd') or '').strip()
    if custom:
        if len(custom) < 6:
            flash('Custom password must be at least 6 characters.', 'danger')
            return redirect(url_for('admin_users'))
        new_pwd = custom
        label = 'custom password'
    elif user['role'] == 'student' and user['matric_number']:
        new_pwd = user['matric_number'].lower().replace('/', '')
        label = 'their registration number (slugified, lowercase, no slashes)'
    elif user['role'] == 'supervisor' and user['staff_id']:
        new_pwd = user['staff_id'].lower().replace('/', '')
        label = 'their staff ID (slugified, lowercase, no slashes)'
    else:
        new_pwd = 'admin123'
        label = 'admin123'
    execute_db("UPDATE users SET password_hash=?, force_pwd_change=1 WHERE id=?",
               (generate_password_hash(new_pwd), uid))
    log_action('password_reset', 'user:' + user['username'],
               'admin set ' + label)
    notify(user['id'], 'Password Reset by Admin',
           'Your password has been reset. New password: ' + new_pwd
           + '. You will be required to change it on next login.',
           url_for('profile'), 'key')
    flash('Password reset. New password: ' + new_pwd, 'success')
    return redirect(url_for('admin_users'))


# ----- Preload (CSV import) ----------------------------------------- #
@app.route('/admin/preload')
@login_required('admin')
def admin_preload():
    """Admin landing page for CSV import + claimed/unclaimed view."""
    s_total = query_db("SELECT COUNT(*) c FROM preload_students", one=True)['c']
    s_claimed = query_db("SELECT COUNT(*) c FROM preload_students WHERE claimed=1", one=True)['c']
    v_total = query_db("SELECT COUNT(*) c FROM preload_supervisors", one=True)['c']
    v_claimed = query_db("SELECT COUNT(*) c FROM preload_supervisors WHERE claimed=1", one=True)['c']
    by_level = query_db("""SELECT level, COUNT(*) c FROM preload_students
                           GROUP BY level ORDER BY level""")
    by_spec = query_db("""SELECT specialization, level, COUNT(*) c FROM preload_students
                          GROUP BY specialization, level ORDER BY level, specialization""")
    recent = query_db("""SELECT * FROM preload_students
                         ORDER BY id DESC LIMIT 10""")
    return render_template('admin/preload.html',
                           s_total=s_total, s_claimed=s_claimed,
                           v_total=v_total, v_claimed=v_claimed,
                           by_level=by_level, by_spec=by_spec, recent=recent)


def _parse_csv_upload(fileobj):
    """Tolerant CSV reader. Returns list of dicts."""
    import csv, io
    raw = fileobj.read()
    if isinstance(raw, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return []
    else:
        text = raw
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        # Normalise keys: lowercase, strip, replace spaces with underscores
        norm = {}
        for k, v in r.items():
            if k is None:
                continue
            nk = k.strip().lower().replace(' ', '_').replace('-', '_')
            norm[nk] = (v or '').strip()
        rows.append(norm)
    return rows


@app.route('/admin/preload/students', methods=['POST'])
@login_required('admin')
def admin_preload_students():
    """Upload CSV of student identity records."""
    if 'file' not in request.files or not request.files['file'].filename:
        flash('Please choose a CSV file to upload.', 'danger')
        return redirect(url_for('admin_preload'))
    f = request.files['file']
    rows = _parse_csv_upload(f)
    if not rows:
        flash('Could not read any rows from the CSV. Ensure it has a header row.', 'danger')
        return redirect(url_for('admin_preload'))

    imported = 0
    skipped_dup = 0
    skipped_bad = 0
    skipped_level = 0
    errors = []

    for idx, r in enumerate(rows, start=2):  # row 1 = header
        reg = (r.get('registration_number') or r.get('reg_no') or
               r.get('reg_number') or r.get('matric_number') or '').strip().upper()
        full_name = r.get('full_name') or r.get('name') or ''
        if not reg or not full_name:
            skipped_bad += 1
            errors.append('Row ' + str(idx) + ': missing reg_no or full_name')
            continue
        level = (r.get('level') or '').strip().upper()
        if level not in ('ND2', 'HND2'):
            skipped_level += 1
            errors.append('Row ' + str(idx) + ': level "' + level + '" not allowed (ND2/HND2 only)')
            continue
        # Check duplicate
        existing = query_db(
            "SELECT id FROM preload_students WHERE upper(registration_number)=?",
            (reg,), one=True)
        if existing:
            skipped_dup += 1
            continue
        spec = (r.get('specialization') or r.get('specialisation') or '').strip() or None
        # Auto-detect spec from reg pattern if blank
        if not spec:
            if '/SW/' in reg.upper():
                spec = 'Software Engineering'
            elif '/NC/' in reg.upper():
                spec = 'Networking & Cloud Computing'
        try:
            email = (r.get('email') or '').strip() or None
            phone = (r.get('phone') or r.get('phone_number') or '').strip() or None
            gender = (r.get('gender') or '').strip().title() or None
            dept = (r.get('department') or 'Computer Science').strip()
            full_name_s = full_name.strip()
            # 1) Insert preload identity record
            execute_db(
                "INSERT INTO preload_students (registration_number,full_name,email,phone,"
                "gender,department,level,specialization) VALUES (?,?,?,?,?,?,?,?)",
                (reg, full_name_s, email, phone, gender, dept, level, spec))
            # 2) Auto-create a "shell" user account so the student appears in
            #    the allocation list immediately, without waiting for them to
            #    register. The account is INACTIVE (cannot log in) and has an
            #    unusable password placeholder. When the student visits the
            #    Register page and enters their reg number, the system flips
            #    is_active=1 and stores the password they choose.
            uname = reg.lower().replace('/', '_')
            # Avoid username collisions (very rare)
            collision = query_db("SELECT id FROM users WHERE username=?", (uname,), one=True)
            if collision:
                uname = uname + '_' + uuid.uuid4().hex[:6]
            shell_pwd = '!unset!' + uuid.uuid4().hex  # unusable hash placeholder
            new_uid = execute_db(
                "INSERT INTO users (username,password_hash,role,full_name,email,phone,gender,"
                "matric_number,department,level,specialization,is_active,force_pwd_change) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uname, generate_password_hash(shell_pwd), 'student', full_name_s,
                 email, phone, gender, reg, dept, level, spec, 0, 0))
            imported += 1
        except sqlite3.IntegrityError:
            skipped_dup += 1
        except Exception as e:
            skipped_bad += 1
            errors.append('Row ' + str(idx) + ': ' + str(e)[:80])

    log_action('preload_students', 'csv',
               'imported=' + str(imported) + ' dup=' + str(skipped_dup)
               + ' bad=' + str(skipped_bad))

    summary = (str(imported) + ' imported, ' + str(skipped_dup) + ' duplicates skipped, '
               + str(skipped_bad) + ' bad rows skipped, ' + str(skipped_level)
               + ' wrong level skipped.')
    if imported:
        flash('Student import complete: ' + summary, 'success')
    else:
        flash('No new students imported. ' + summary, 'warning')
    if errors[:3]:
        flash('First issues: ' + ' | '.join(errors[:3]), 'info')
    return redirect(url_for('admin_preload'))


@app.route('/admin/preload/supervisors', methods=['POST'])
@login_required('admin')
def admin_preload_supervisors():
    if 'file' not in request.files or not request.files['file'].filename:
        flash('Please choose a CSV file to upload.', 'danger')
        return redirect(url_for('admin_preload'))
    rows = _parse_csv_upload(request.files['file'])
    if not rows:
        flash('Could not read CSV.', 'danger')
        return redirect(url_for('admin_preload'))
    imported, dup, bad = 0, 0, 0
    errors = []
    for idx, r in enumerate(rows, start=2):
        sid = (r.get('staff_id') or r.get('staff_no') or r.get('staff_number') or '').strip().upper()
        full_name = r.get('full_name') or r.get('name') or ''
        if not sid or not full_name:
            bad += 1
            errors.append('Row ' + str(idx) + ': missing staff_id or full_name')
            continue
        if query_db("SELECT id FROM preload_supervisors WHERE upper(staff_id)=?",
                    (sid,), one=True):
            dup += 1
            continue
        try:
            email = (r.get('email') or '').strip() or None
            phone = (r.get('phone') or '').strip() or None
            title = (r.get('title') or '').strip() or None
            gender = (r.get('gender') or '').strip().title() or None
            dept = (r.get('department') or 'Computer Science').strip()
            full_name_s = full_name.strip()
            # 1) Insert preload identity record
            execute_db(
                "INSERT INTO preload_supervisors (staff_id,full_name,title,email,phone,"
                "gender,department) VALUES (?,?,?,?,?,?,?)",
                (sid, full_name_s, title, email, phone, gender, dept))
            # 2) Auto-create a "shell" user account so the supervisor appears
            #    in the allocation supervisor dropdown immediately. INACTIVE
            #    until they register (set a password) for themselves.
            uname = sid.lower().replace('/', '_')
            collision = query_db("SELECT id FROM users WHERE username=?", (uname,), one=True)
            if collision:
                uname = uname + '_' + uuid.uuid4().hex[:6]
            shell_pwd = '!unset!' + uuid.uuid4().hex
            execute_db(
                "INSERT INTO users (username,password_hash,role,full_name,email,phone,title,"
                "gender,staff_id,department,is_active,force_pwd_change) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uname, generate_password_hash(shell_pwd), 'supervisor', full_name_s,
                 email, phone, title, gender, sid, dept, 0, 0))
            imported += 1
        except sqlite3.IntegrityError:
            dup += 1
        except Exception as e:
            bad += 1
            errors.append('Row ' + str(idx) + ': ' + str(e)[:80])
    log_action('preload_supervisors', 'csv',
               'imported=' + str(imported) + ' dup=' + str(dup) + ' bad=' + str(bad))
    summary = (str(imported) + ' imported, ' + str(dup) + ' duplicates skipped, '
               + str(bad) + ' bad rows skipped.')
    flash(('Supervisor import complete: ' if imported else 'No new supervisors imported. ')
          + summary, 'success' if imported else 'warning')
    if errors[:3]:
        flash('First issues: ' + ' | '.join(errors[:3]), 'info')
    return redirect(url_for('admin_preload'))


@app.route('/admin/preload/sample/<kind>')
@login_required('admin')
def admin_preload_sample(kind):
    """Download a sample CSV template."""
    import csv, io
    out = io.StringIO()
    w = csv.writer(out)
    if kind == 'students':
        w.writerow(['registration_number', 'full_name', 'email', 'phone',
                    'gender', 'department', 'level', 'specialization'])
        w.writerow(['FPN/S05/2024/2025/CS/SW/001', 'John Doe', 'john@stu.fpn.edu.ng',
                    '08031234567', 'Male', 'Computer Science', 'HND2',
                    'Software Engineering'])
        w.writerow(['FPN/S05/2024/2025/CS/NC/001', 'Jane Smith', 'jane@stu.fpn.edu.ng',
                    '08031234568', 'Female', 'Computer Science', 'HND2',
                    'Networking & Cloud Computing'])
        w.writerow(['FPN/S05/2024/2025/CS/001', 'Peter Okoro', 'peter@stu.fpn.edu.ng',
                    '08031234569', 'Male', 'Computer Science', 'ND2', ''])
        filename = 'students_template.csv'
    elif kind == 'supervisors':
        w.writerow(['staff_id', 'full_name', 'title', 'email', 'phone', 'gender', 'department'])
        w.writerow(['FPN/STF/CS/001', 'Mebawondu Joseph O.', 'Dr',
                    'jmebawondu@fpn.edu.ng', '08031000001', 'Male', 'Computer Science'])
        w.writerow(['FPN/STF/CS/002', 'Ibrahim Abdullahi', 'Engr',
                    'aibrahim@fpn.edu.ng', '08031000002', 'Male', 'Computer Science'])
        filename = 'supervisors_template.csv'
    else:
        abort(404)
    from flask import Response
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=' + filename})


@app.route('/admin/preload/list/<kind>')
@login_required('admin')
def admin_preload_list(kind):
    """Browse all preloaded records of one kind."""
    if kind not in ('students', 'supervisors'):
        abort(404)
    status = request.args.get('status', 'all')
    q = (request.args.get('q', '') or '').strip()
    if kind == 'students':
        sql = "SELECT * FROM preload_students WHERE 1=1"
        args = []
        if status == 'claimed':
            sql += " AND claimed=1"
        elif status == 'unclaimed':
            sql += " AND claimed=0"
        if q:
            sql += (" AND (full_name LIKE ? OR registration_number LIKE ? "
                    "OR email LIKE ?)")
            ql = '%' + q + '%'
            args += [ql, ql, ql]
        sql += " ORDER BY registration_number"
        rows = query_db(sql, args)
    else:
        sql = "SELECT * FROM preload_supervisors WHERE 1=1"
        args = []
        if status == 'claimed':
            sql += " AND claimed=1"
        elif status == 'unclaimed':
            sql += " AND claimed=0"
        if q:
            sql += " AND (full_name LIKE ? OR staff_id LIKE ? OR email LIKE ?)"
            ql = '%' + q + '%'
            args += [ql, ql, ql]
        sql += " ORDER BY staff_id"
        rows = query_db(sql, args)
    return render_template('admin/preload_list.html', kind=kind, rows=rows,
                           status=status, q=q)


@app.route('/admin/preload/<kind>/<int:rid>/delete', methods=['POST'])
@login_required('admin')
def admin_preload_delete(kind, rid):
    if kind not in ('students', 'supervisors'):
        abort(404)
    tbl = 'preload_' + kind
    rec = query_db("SELECT * FROM " + tbl + " WHERE id=?", (rid,), one=True)
    if not rec:
        abort(404)
    if rec['claimed']:
        flash('Cannot delete a claimed record (account already created).', 'danger')
        return redirect(url_for('admin_preload_list', kind=kind))
    execute_db("DELETE FROM " + tbl + " WHERE id=?", (rid,))
    flash('Pre-load record removed.', 'success')
    return redirect(url_for('admin_preload_list', kind=kind))


# ----- Allocations --------------------------------------------------- #
@app.route('/admin/allocations')
@login_required('admin')
def admin_allocations():
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    if not cur:
        flash('No active session. Create one first.', 'warning')
        return redirect(url_for('admin_sessions'))

    # Filters for unallocated list
    f_level = request.args.get('level', 'all')   # all / ND2 / HND2
    f_spec  = request.args.get('spec', 'all')    # all / Software... / Networking...

    rows = query_db("""SELECT a.*, s.full_name AS student_name, s.matric_number,
                              s.level AS student_level, s.specialization AS student_spec,
                              sv.full_name AS sup_name, sv.title AS sup_title
                       FROM allocations a
                       JOIN users s  ON s.id  = a.student_id
                       JOIN users sv ON sv.id = a.supervisor_id
                       WHERE a.session_id=? AND a.status='active'
                       ORDER BY sv.full_name, s.full_name""", (cur['id'],))

    where_extra = ""
    args = [cur['id']]
    if f_level in ('ND2', 'HND2'):
        where_extra += " AND u.level=?"
        args.append(f_level)
    if f_spec != 'all':
        where_extra += " AND u.specialization=?"
        args.append(f_spec)
    unallocated = query_db("""SELECT u.* FROM users u
                              WHERE u.role='student'
                              AND u.level IN ('ND2','HND2')
                              AND NOT EXISTS (SELECT 1 FROM allocations a
                                              WHERE a.student_id=u.id
                                                AND a.session_id=?
                                                AND a.status='active')""" + where_extra + """
                              ORDER BY u.level, u.specialization, u.full_name""", args)

    supervisors = query_db("""SELECT sv.*, COUNT(a.id) AS load
                              FROM users sv
                              LEFT JOIN allocations a ON a.supervisor_id=sv.id
                                       AND a.session_id=? AND a.status='active'
                              WHERE sv.role='supervisor'
                              GROUP BY sv.id
                              ORDER BY load, sv.full_name""", (cur['id'],))
    # Stats
    total_alloc = len(rows)
    total_unalloc = len(unallocated)

    return render_template('admin/allocations.html',
                           allocations=rows, unallocated=unallocated,
                           supervisors=supervisors, session=cur,
                           f_level=f_level, f_spec=f_spec,
                           total_alloc=total_alloc, total_unalloc=total_unalloc)


@app.route('/admin/allocations/assign', methods=['POST'])
@login_required('admin')
def admin_allocate():
    """Allocate one or many students (from checkboxes) to one supervisor."""
    supervisor_id = int(request.form.get('supervisor_id', 0) or 0)
    session_id = int(request.form.get('session_id', 0) or 0)
    # Accept either a single student_id or many student_ids[] (bulk select)
    sids_raw = request.form.getlist('student_ids')
    if not sids_raw:
        single = request.form.get('student_id', '')
        if single:
            sids_raw = [single]
    try:
        student_ids = [int(s) for s in sids_raw if s]
    except ValueError:
        student_ids = []

    if not (supervisor_id and session_id and student_ids):
        flash('Pick at least one student and a supervisor.', 'danger')
        return redirect(url_for('admin_allocations'))

    sup = query_db("SELECT * FROM users WHERE id=? AND role='supervisor'",
                   (supervisor_id,), one=True)
    if not sup:
        flash('Supervisor not found.', 'danger')
        return redirect(url_for('admin_allocations'))

    allocated = 0
    skipped = 0
    skipped_names = []
    for sid in student_ids:
        existing = query_db(
            "SELECT a.id, sv.full_name AS sup_name FROM allocations a "
            "JOIN users sv ON sv.id=a.supervisor_id "
            "WHERE a.student_id=? AND a.session_id=? AND a.status='active'",
            (sid, session_id), one=True)
        if existing:
            skipped += 1
            stu = query_db("SELECT full_name FROM users WHERE id=?", (sid,), one=True)
            if stu:
                skipped_names.append(stu['full_name'] + ' -> ' + existing['sup_name'])
            continue
        execute_db(
            "INSERT INTO allocations (student_id,supervisor_id,session_id,allocated_by) "
            "VALUES (?,?,?,?)",
            (sid, supervisor_id, session_id, current_user()['id'])
        )
        stu = query_db("SELECT * FROM users WHERE id=?", (sid,), one=True)
        notify(sid, 'Supervisor Allocated',
               'You have been assigned to ' + (sup['title'] or '') + ' ' + sup['full_name']
               + '. Log in to submit your topic.', url_for('dashboard'), 'user-check')
        notify(supervisor_id, 'New Supervisee',
               stu['full_name'] + ' (' + (stu['matric_number'] or '-')
               + ') has been allocated to you.',
               url_for('dashboard'), 'users')
        log_action('allocate', 'student:' + stu['username'],
                   'to ' + sup['username'])
        allocated += 1

    if allocated and skipped:
        flash(str(allocated) + ' allocated. '
              + str(skipped) + ' skipped (already allocated): '
              + '; '.join(skipped_names[:3])
              + ('…' if len(skipped_names) > 3 else ''), 'warning')
    elif allocated:
        flash(str(allocated) + ' student(s) allocated to '
              + (sup['title'] or '') + ' ' + sup['full_name'] + '.', 'success')
    else:
        flash('No allocations made. All selected students already have supervisors.', 'warning')
    return redirect(url_for('admin_allocations'))


@app.route('/admin/allocations/<int:aid>/reassign', methods=['POST'])
@login_required('admin')
def admin_reassign(aid):
    new_sup = int(request.form.get('supervisor_id', 0) or 0)
    a = query_db("SELECT * FROM allocations WHERE id=?", (aid,), one=True)
    if not a or not new_sup:
        flash('Choose a supervisor to reassign to.', 'danger')
        return redirect(url_for('admin_allocations'))
    if new_sup == a['supervisor_id']:
        flash('That is the same supervisor.', 'info')
        return redirect(url_for('admin_allocations'))
    execute_db("UPDATE allocations SET status='reassigned' WHERE id=?", (aid,))
    execute_db("INSERT INTO allocations (student_id,supervisor_id,session_id,allocated_by) "
               "VALUES (?,?,?,?)",
               (a['student_id'], new_sup, a['session_id'], current_user()['id']))
    sup = query_db("SELECT * FROM users WHERE id=?", (new_sup,), one=True)
    notify(a['student_id'], 'Supervisor Reassigned',
           'Your supervisor has been changed to ' + (sup['title'] or '') + ' '
           + sup['full_name'] + '.',
           url_for('dashboard'), 'rotate')
    notify(new_sup, 'New Supervisee (Reassigned)',
           'A student has been reassigned to you.', url_for('dashboard'), 'users')
    log_action('reassign', 'allocation:' + str(aid), 'new sup ' + str(new_sup))
    flash('Reassigned successfully.', 'success')
    return redirect(url_for('admin_allocations'))


@app.route('/admin/allocations/auto', methods=['POST'])
@login_required('admin')
def admin_auto_allocate():
    """Evenly distribute all unallocated ND2/HND2 students across active supervisors.
       No cap. Lowest-load-first to keep the distribution balanced even if some
       supervisors already have supervisees."""
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    if not cur:
        flash('No active session.', 'danger')
        return redirect(url_for('admin_allocations'))
    students = query_db("""SELECT u.id, u.full_name FROM users u
                           WHERE u.role='student'
                             AND u.level IN ('ND2','HND2')
                             AND NOT EXISTS (SELECT 1 FROM allocations a
                                             WHERE a.student_id=u.id
                                               AND a.session_id=?
                                               AND a.status='active')
                           ORDER BY u.full_name""", (cur['id'],))
    sups = query_db("""SELECT sv.id, sv.full_name, COUNT(a.id) AS load FROM users sv
                       LEFT JOIN allocations a ON a.supervisor_id=sv.id
                                AND a.session_id=? AND a.status='active'
                       WHERE sv.role='supervisor'
                       GROUP BY sv.id ORDER BY load""", (cur['id'],))
    if not sups:
        flash('No active supervisors available.', 'danger')
        return redirect(url_for('admin_allocations'))
    if not students:
        flash('No unallocated students to distribute.', 'info')
        return redirect(url_for('admin_allocations'))

    counts = {s['id']: s['load'] for s in sups}
    sup_ids = [s['id'] for s in sups]
    assigned = 0
    me = current_user()['id']
    for st in students:
        # always pick supervisor with the lowest current load
        sid = min(sup_ids, key=lambda x: counts[x])
        execute_db(
            "INSERT INTO allocations (student_id,supervisor_id,session_id,allocated_by) "
            "VALUES (?,?,?,?)",
            (st['id'], sid, cur['id'], me)
        )
        counts[sid] += 1
        assigned += 1
        notify(st['id'], 'Supervisor Allocated',
               'You have been auto-allocated a project supervisor.',
               url_for('dashboard'), 'user-check')
    # Aggregate notification to each supervisor (one notif each, not per student)
    new_loads_per_sup = {}
    for s in sups:
        new_loads_per_sup[s['id']] = counts[s['id']] - s['load']
    for sid, gained in new_loads_per_sup.items():
        if gained > 0:
            notify(sid, 'New Supervisees',
                   str(gained) + ' student(s) have been allocated to you.',
                   url_for('dashboard'), 'users')
    log_action('auto_allocate', 'session:' + cur['session_name'],
               str(assigned) + ' students across ' + str(len(sup_ids)) + ' supervisors')
    flash(str(assigned) + ' student(s) evenly distributed across '
          + str(len(sup_ids)) + ' supervisor(s).', 'success')
    return redirect(url_for('admin_allocations'))


# ----- Sessions ------------------------------------------------------ #
@app.route('/admin/sessions', methods=['GET', 'POST'])
@login_required('admin')
def admin_sessions():
    if request.method == 'POST':
        f = request.form
        name = f.get('session_name', '').strip()
        if name and not query_db("SELECT 1 FROM sessions WHERE session_name=?", (name,), one=True):
            if f.get('is_current') == 'on':
                execute_db("UPDATE sessions SET is_current=0", ())
            execute_db("""INSERT INTO sessions (session_name,semester,start_date,end_date,is_current)
                          VALUES (?,?,?,?,?)""",
                       (name, f.get('semester'), f.get('start_date') or None,
                        f.get('end_date') or None,
                        1 if f.get('is_current') == 'on' else 0))
            log_action('create_session', name, '')
            flash('Session created.', 'success')
        else:
            flash('Session name missing or already exists.', 'warning')
        return redirect(url_for('admin_sessions'))
    sessions_ = query_db("SELECT * FROM sessions ORDER BY id DESC")
    return render_template('admin/sessions.html', sessions=sessions_)


@app.route('/admin/sessions/<int:sid>/activate', methods=['POST'])
@login_required('admin')
def admin_activate_session(sid):
    execute_db("UPDATE sessions SET is_current=0", ())
    execute_db("UPDATE sessions SET is_current=1 WHERE id=?", (sid,))
    log_action('activate_session', 'session:' + str(sid), '')
    flash('Session activated.', 'success')
    return redirect(url_for('admin_sessions'))


# ----- Announcements ------------------------------------------------- #
@app.route('/admin/announcements', methods=['GET', 'POST'])
@login_required('admin')
def admin_announcements():
    if request.method == 'POST':
        f = request.form
        execute_db("INSERT INTO announcements (title,body,audience,created_by) VALUES (?,?,?,?)",
                   (f.get('title'), f.get('body'), f.get('audience', 'all'),
                    current_user()['id']))
        # broadcast notifications
        audience = f.get('audience', 'all')
        if audience == 'all':
            recips = query_db("SELECT id FROM users WHERE role IN ('student','supervisor')")
        elif audience == 'students':
            recips = query_db("SELECT id FROM users WHERE role='student'")
        else:
            recips = query_db("SELECT id FROM users WHERE role='supervisor'")
        for r in recips:
            notify(r['id'], 'Announcement: ' + f.get('title', ''), f.get('body', ''),
                   url_for('notifications'), 'megaphone')
        flash('Announcement posted.', 'success')
        return redirect(url_for('admin_announcements'))
    items = query_db("""SELECT a.*, u.full_name FROM announcements a
                        LEFT JOIN users u ON u.id=a.created_by
                        ORDER BY a.created_at DESC""")
    return render_template('admin/announcements.html', items=items)


# ----- Reports ------------------------------------------------------- #
@app.route('/admin/reports')
@login_required('admin')
def admin_reports():
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    sessions_ = query_db("SELECT * FROM sessions ORDER BY id DESC")
    return render_template('admin/reports.html', current=cur, sessions=sessions_)


@app.route('/admin/reports/view')
@login_required('admin')
def admin_report_view():
    sid = request.args.get('session_id')
    rtype = request.args.get('type', 'overview')
    if not sid:
        cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
        sid = cur['id'] if cur else None
    if not sid:
        flash('No session available.', 'warning')
        return redirect(url_for('admin_reports'))
    sid = int(sid)
    sess = query_db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)

    data = {}
    data['session'] = sess
    data['stats'] = {
        'students': query_db("""SELECT COUNT(DISTINCT a.student_id) c FROM allocations a
                                WHERE a.session_id=?""", (sid,), one=True)['c'],
        'supervisors': query_db("""SELECT COUNT(DISTINCT a.supervisor_id) c FROM allocations a
                                   WHERE a.session_id=?""", (sid,), one=True)['c'],
        'topics_submitted': query_db("SELECT COUNT(*) c FROM topics WHERE session_id=?",
                                     (sid,), one=True)['c'],
        'topics_approved':  query_db("SELECT COUNT(*) c FROM topics WHERE session_id=? AND status='approved'",
                                     (sid,), one=True)['c'],
        'topics_rejected':  query_db("SELECT COUNT(*) c FROM topics WHERE session_id=? AND status='rejected'",
                                     (sid,), one=True)['c'],
        'chapters_total':   query_db("""SELECT COUNT(*) c FROM chapters ch
                                        JOIN topics t ON t.id=ch.topic_id
                                        WHERE t.session_id=?""", (sid,), one=True)['c'],
        'chapters_approved': query_db("""SELECT COUNT(*) c FROM chapters ch
                                         JOIN topics t ON t.id=ch.topic_id
                                         WHERE t.session_id=? AND ch.status='approved'""",
                                      (sid,), one=True)['c'],
        'defences_completed': query_db("""SELECT COUNT(*) c FROM defences d
                                          JOIN topics t ON t.id=d.topic_id
                                          WHERE t.session_id=? AND d.status='completed'""",
                                       (sid,), one=True)['c'],
    }
    data['supervisor_perf'] = query_db("""
        SELECT sv.id, sv.title, sv.full_name,
               COUNT(DISTINCT a.student_id) AS supervisees,
               SUM(CASE WHEN t.status='approved' THEN 1 ELSE 0 END) AS approved_topics,
               SUM(CASE WHEN d.status='completed' THEN 1 ELSE 0 END) AS completed
        FROM users sv
        LEFT JOIN allocations a ON a.supervisor_id=sv.id AND a.session_id=? AND a.status='active'
        LEFT JOIN topics t ON t.supervisor_id=sv.id AND t.session_id=?
        LEFT JOIN defences d ON d.supervisor_id=sv.id
        WHERE sv.role='supervisor'
        GROUP BY sv.id
        ORDER BY supervisees DESC
    """, (sid, sid))
    data['student_progress'] = query_db("""
        SELECT s.id, s.full_name, s.matric_number,
               sv.full_name AS sup_name, sv.title AS sup_title,
               (SELECT title FROM topics WHERE student_id=s.id AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_title,
               (SELECT status FROM topics WHERE student_id=s.id AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_status,
               (SELECT COUNT(DISTINCT chapter_no) FROM chapters c JOIN topics tt ON tt.id=c.topic_id
                WHERE c.student_id=s.id AND tt.session_id=? AND c.status='approved') AS approved_chapters,
               (SELECT total_score FROM defences d JOIN topics tt ON tt.id=d.topic_id
                WHERE d.student_id=s.id AND tt.session_id=?) AS defence_score
        FROM users s
        JOIN allocations a ON a.student_id=s.id AND a.session_id=? AND a.status='active'
        JOIN users sv ON sv.id=a.supervisor_id
        WHERE s.role='student'
        ORDER BY s.full_name
    """, (sid, sid, sid, sid, sid))
    data['grade_distribution'] = query_db("""
        SELECT d.grade, COUNT(*) c FROM defences d
        JOIN topics t ON t.id=d.topic_id
        WHERE t.session_id=? AND d.status='completed'
        GROUP BY d.grade
    """, (sid,))

    return render_template('admin/report_view.html', data=data,
                           rtype=rtype, sessions=query_db("SELECT * FROM sessions ORDER BY id DESC"))


@app.route('/admin/reports/csv')
@login_required('admin')
def admin_report_csv():
    sid = int(request.args.get('session_id'))
    rtype = request.args.get('type', 'students')
    sess = query_db("SELECT * FROM sessions WHERE id=?", (sid,), one=True)

    output = io.StringIO()
    writer = csv.writer(output)
    if rtype == 'students':
        writer.writerow(['Matric No', 'Student', 'Supervisor', 'Topic',
                         'Topic Status', 'Approved Chapters', 'Defence Score', 'Grade'])
        rows = query_db("""
            SELECT s.full_name, s.matric_number, sv.full_name sup_name, sv.title sup_title,
                   (SELECT title FROM topics WHERE student_id=s.id AND session_id=? ORDER BY id DESC LIMIT 1) tt,
                   (SELECT status FROM topics WHERE student_id=s.id AND session_id=? ORDER BY id DESC LIMIT 1) ts,
                   (SELECT COUNT(DISTINCT chapter_no) FROM chapters c JOIN topics tp ON tp.id=c.topic_id
                    WHERE c.student_id=s.id AND tp.session_id=? AND c.status='approved') ac,
                   (SELECT total_score FROM defences d JOIN topics tp ON tp.id=d.topic_id
                    WHERE d.student_id=s.id AND tp.session_id=?) ds
            FROM users s
            JOIN allocations a ON a.student_id=s.id AND a.session_id=? AND a.status='active'
            JOIN users sv ON sv.id=a.supervisor_id
            WHERE s.role='student' ORDER BY s.full_name
        """, (sid, sid, sid, sid, sid))
        for r in rows:
            writer.writerow([r['matric_number'] or '', r['full_name'],
                             (r['sup_title'] or '') + ' ' + r['sup_name'],
                             r['tt'] or '', r['ts'] or '',
                             r['ac'] or 0, r['ds'] or '',
                             grade_from_score(r['ds']) if r['ds'] else ''])
    elif rtype == 'supervisors':
        writer.writerow(['Supervisor', 'Supervisees', 'Approved Topics', 'Completed Defences'])
        rows = query_db("""
            SELECT sv.title, sv.full_name,
                   COUNT(DISTINCT a.student_id) sup_count,
                   SUM(CASE WHEN t.status='approved' THEN 1 ELSE 0 END) ap,
                   SUM(CASE WHEN d.status='completed' THEN 1 ELSE 0 END) cm
            FROM users sv
            LEFT JOIN allocations a ON a.supervisor_id=sv.id AND a.session_id=? AND a.status='active'
            LEFT JOIN topics t ON t.supervisor_id=sv.id AND t.session_id=?
            LEFT JOIN defences d ON d.supervisor_id=sv.id
            WHERE sv.role='supervisor' GROUP BY sv.id
        """, (sid, sid))
        for r in rows:
            writer.writerow([(r['title'] or '') + ' ' + r['full_name'],
                             r['sup_count'] or 0, r['ap'] or 0, r['cm'] or 0])

    out = output.getvalue().encode('utf-8')
    fname = 'UPAMS_' + rtype + '_' + sess['session_name'].replace('/', '-') + '.csv'
    return Response(out, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=' + fname})


@app.route('/admin/audit')
@login_required('admin')
def admin_audit():
    logs = query_db("""SELECT a.*, u.full_name, u.role FROM audit_log a
                       LEFT JOIN users u ON u.id=a.user_id
                       ORDER BY a.created_at DESC LIMIT 200""")
    return render_template('admin/audit.html', logs=logs)


# --------------------------------------------------------------------- #
# SUPERVISOR ========================================================== #
# --------------------------------------------------------------------- #
def supervisor_dashboard():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    sid = cur['id'] if cur else 0
    stats = {
        'students':       query_db("""SELECT COUNT(*) c FROM allocations
                                      WHERE supervisor_id=? AND session_id=? AND status='active'""",
                                   (me['id'], sid), one=True)['c'],
        'pending_topics': query_db("""SELECT COUNT(*) c FROM topics
                                      WHERE supervisor_id=? AND status='pending'""",
                                   (me['id'],), one=True)['c'],
        'pending_chaps':  query_db("""SELECT COUNT(*) c FROM chapters
                                      WHERE supervisor_id=? AND status='submitted'""",
                                   (me['id'],), one=True)['c'],
        'approved_chaps': query_db("""SELECT COUNT(*) c FROM chapters
                                      WHERE supervisor_id=? AND status='approved'""",
                                   (me['id'],), one=True)['c'],
        'defences_done':  query_db("""SELECT COUNT(*) c FROM defences
                                      WHERE supervisor_id=? AND status='completed'""",
                                   (me['id'],), one=True)['c'],
    }
    my_students = query_db("""
        SELECT s.id, s.full_name, s.matric_number, s.level,
               (SELECT title FROM topics WHERE student_id=s.id AND supervisor_id=?
                AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_title,
               (SELECT status FROM topics WHERE student_id=s.id AND supervisor_id=?
                AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_status,
               (SELECT COUNT(DISTINCT chapter_no) FROM chapters c JOIN topics t ON t.id=c.topic_id
                WHERE c.student_id=s.id AND c.status='approved' AND t.session_id=?) AS chaps
        FROM users s
        JOIN allocations a ON a.student_id=s.id
        WHERE a.supervisor_id=? AND a.session_id=? AND a.status='active' AND s.role='student'
        ORDER BY s.full_name
    """, (me['id'], sid, me['id'], sid, sid, me['id'], sid))
    pending_topics = query_db("""
        SELECT t.*, s.full_name AS student_name, s.matric_number
        FROM topics t JOIN users s ON s.id=t.student_id
        WHERE t.supervisor_id=? AND t.status='pending'
        ORDER BY t.created_at DESC
    """, (me['id'],))
    pending_chapters = query_db("""
        SELECT c.*, s.full_name AS student_name, t.title AS topic_title
        FROM chapters c JOIN users s ON s.id=c.student_id
        JOIN topics t ON t.id=c.topic_id
        WHERE c.supervisor_id=? AND c.status='submitted'
        ORDER BY c.submitted_at DESC LIMIT 8
    """, (me['id'],))
    return render_template('supervisor/dashboard.html',
                           stats=stats, students=my_students,
                           pending_topics=pending_topics,
                           pending_chapters=pending_chapters)


@app.route('/supervisor/students')
@login_required('supervisor')
def sup_students():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    sid = cur['id'] if cur else 0
    students = query_db("""
        SELECT s.*,
               (SELECT id    FROM topics WHERE student_id=s.id AND supervisor_id=? AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_id,
               (SELECT title FROM topics WHERE student_id=s.id AND supervisor_id=? AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_title,
               (SELECT status FROM topics WHERE student_id=s.id AND supervisor_id=? AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_status,
               (SELECT COUNT(DISTINCT chapter_no) FROM chapters c JOIN topics t ON t.id=c.topic_id
                WHERE c.student_id=s.id AND c.status='approved' AND t.session_id=?) AS chaps
        FROM users s
        JOIN allocations a ON a.student_id=s.id
        WHERE a.supervisor_id=? AND a.session_id=? AND a.status='active' AND s.role='student'
        ORDER BY s.full_name
    """, (me['id'], sid, me['id'], sid, me['id'], sid, sid, me['id'], sid))
    return render_template('supervisor/students.html', students=students)


@app.route('/supervisor/topics')
@login_required('supervisor')
def sup_topics():
    me = current_user()
    status = request.args.get('status', 'pending')
    sql = """SELECT t.*, s.full_name AS student_name, s.matric_number
             FROM topics t JOIN users s ON s.id=t.student_id
             WHERE t.supervisor_id=?"""
    args = [me['id']]
    if status != 'all':
        sql += " AND t.status=?"
        args.append(status)
    sql += " ORDER BY t.created_at DESC"
    topics = query_db(sql, args)
    return render_template('supervisor/topics.html', topics=topics, status=status)


@app.route('/supervisor/topics/<int:tid>/decide', methods=['POST'])
@login_required('supervisor')
def sup_topic_decide(tid):
    me = current_user()
    t = query_db("SELECT * FROM topics WHERE id=? AND supervisor_id=?", (tid, me['id']), one=True)
    if not t:
        abort(404)
    decision = request.form.get('decision')
    note = request.form.get('note', '').strip()
    if decision not in ('approved', 'rejected'):
        flash('Invalid decision.', 'danger')
        return redirect(url_for('sup_topics'))
    if decision == 'approved':
        conflict = find_topic_conflict(t['title'], t['session_id'],
                                       exclude_student_id=t['student_id'],
                                       statuses=('approved',))
        if conflict:
            who = conflict['conflict_student_name'] or 'another student'
            if conflict['conflict_student_matric']:
                who = who + ' (' + conflict['conflict_student_matric'] + ')'
            flash('You cannot approve this topic. "' + t['title'] + '" is already '
                  'an approved topic for ' + who + '. Ask the student to revise it '
                  'to a unique topic, or reject this submission instead.', 'danger')
            return redirect(url_for('sup_topics'))
    execute_db("""UPDATE topics SET status=?, supervisor_note=?, decided_at=? WHERE id=?""",
               (decision, note, datetime.now(), tid))
    notify(t['student_id'],
           'Topic ' + decision.capitalize(),
           'Your topic "' + t['title'] + '" was ' + decision +
           ('. Note: ' + note if note else '.'),
           url_for('std_topic'), 'check' if decision == 'approved' else 'x')
    log_action('topic_' + decision, 'topic:' + str(tid), note[:80])
    flash('Topic ' + decision + '.', 'success')
    return redirect(url_for('sup_topics'))


@app.route('/supervisor/topics/propose', methods=['GET', 'POST'])
@login_required('supervisor')
def sup_topic_propose():
    """Supervisor proposes a topic FOR a student (the FPN-style workflow)."""
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    if not cur:
        flash('No active academic session. Ask the admin to open one first.', 'warning')
        return redirect(url_for('sup_topics'))
    if request.method == 'POST':
        f = request.form
        stu_id = int(f.get('student_id', 0))
        # ensure student is one of mine
        a = query_db("""SELECT 1 FROM allocations WHERE student_id=? AND supervisor_id=?
                        AND session_id=? AND status='active'""",
                     (stu_id, me['id'], cur['id']), one=True)
        if not a:
            flash('That student is not allocated to you.', 'danger')
            return redirect(url_for('sup_topic_propose'))
        proposed_title = f.get('title', '').strip()
        if not proposed_title:
            flash('Please enter a topic title before assigning.', 'warning')
            return redirect(url_for('sup_topic_propose'))
        # Block re-using a topic already proposed/approved for a different student.
        conflict = find_topic_conflict(proposed_title, cur['id'],
                                       exclude_student_id=stu_id)
        if conflict:
            who = conflict['conflict_student_name'] or 'another student'
            if conflict['conflict_student_matric']:
                who = who + ' (' + conflict['conflict_student_matric'] + ')'
            flash('You cannot propose this topic. "' + conflict['title'] + '" has '
                  + ('already been approved' if conflict['status'] == 'approved'
                     else 'already been proposed/submitted')
                  + ' for ' + who + '. Every student must have a unique project '
                  'topic \u2014 please assign a different topic to this student.', 'danger')
            return redirect(url_for('sup_topic_propose'))
        execute_db("""INSERT INTO topics (student_id,supervisor_id,session_id,title,background,
                                          objectives,methodology,submitted_by,status,decided_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (stu_id, me['id'], cur['id'], f.get('title'), f.get('background'),
                    f.get('objectives'), f.get('methodology'), 'supervisor',
                    'approved', datetime.now()))
        notify(stu_id, 'Topic Assigned by Supervisor',
               'Your supervisor has assigned a topic: ' + f.get('title', ''),
               url_for('std_topic'), 'clipboard')
        log_action('topic_propose', 'student:' + str(stu_id), f.get('title', ''))
        flash('Topic proposed and assigned to student.', 'success')
        return redirect(url_for('sup_topics', status='all'))
    students = query_db("""SELECT s.id, s.full_name, s.matric_number FROM users s
                           JOIN allocations a ON a.student_id=s.id
                           WHERE a.supervisor_id=? AND a.session_id=? AND a.status='active'
                             AND NOT EXISTS (SELECT 1 FROM topics t
                                             WHERE t.student_id=s.id AND t.session_id=?
                                               AND t.status IN ('pending','approved'))
                           ORDER BY s.full_name""",
                        (me['id'], cur['id'] if cur else 0, cur['id'] if cur else 0))
    return render_template('supervisor/propose_topic.html', students=students)


@app.route('/supervisor/chapters')
@login_required('supervisor')
def sup_chapters():
    me = current_user()
    status = request.args.get('status', 'submitted')
    sql = """SELECT c.*, s.full_name AS student_name, s.matric_number,
                    t.title AS topic_title
             FROM chapters c
             JOIN users s ON s.id=c.student_id
             JOIN topics t ON t.id=c.topic_id
             WHERE c.supervisor_id=?"""
    args = [me['id']]
    if status != 'all':
        sql += " AND c.status=?"
        args.append(status)
    sql += " ORDER BY c.submitted_at DESC"
    rows = query_db(sql, args)
    return render_template('supervisor/chapters.html', chapters=rows, status=status)


@app.route('/supervisor/chapters/<int:cid>/review', methods=['GET', 'POST'])
@login_required('supervisor')
def sup_chapter_review(cid):
    me = current_user()
    ch = query_db("""SELECT c.*, s.full_name AS student_name, s.matric_number,
                            t.title AS topic_title
                     FROM chapters c JOIN users s ON s.id=c.student_id
                     JOIN topics t ON t.id=c.topic_id
                     WHERE c.id=? AND c.supervisor_id=?""",
                  (cid, me['id']), one=True)
    if not ch:
        abort(404)
    if request.method == 'POST':
        decision = request.form.get('decision')
        note = request.form.get('correction_note', '').strip()
        grade = request.form.get('grade')
        try:
            grade = int(grade) if grade else None
        except ValueError:
            grade = None
        if decision not in ('approved', 'corrections', 'rejected'):
            flash('Invalid decision.', 'danger')
            return redirect(url_for('sup_chapter_review', cid=cid))
        execute_db("""UPDATE chapters SET status=?, correction_note=?, grade=?,
                                          reviewed_at=? WHERE id=?""",
                   (decision, note, grade, datetime.now(), cid))
        msg = {
            'approved': 'has been APPROVED',
            'corrections': 'needs CORRECTIONS',
            'rejected': 'has been REJECTED',
        }[decision]
        notify(ch['student_id'],
               'Chapter ' + str(ch['chapter_no']) + ' ' + msg,
               'Topic: ' + ch['topic_title']
               + ('. Note: ' + note if note else '.'),
               url_for('std_chapters'),
               'check' if decision == 'approved' else 'pen')
        log_action('chapter_' + decision, 'chapter:' + str(cid), note[:80])
        flash('Chapter review saved.', 'success')
        return redirect(url_for('sup_chapters'))
    all_versions = query_db("""SELECT * FROM chapters WHERE topic_id=? AND chapter_no=?
                               ORDER BY version DESC""",
                            (ch['topic_id'], ch['chapter_no']))
    return render_template('supervisor/chapter_review.html', chapter=ch, all_versions=all_versions)


@app.route('/supervisor/defences')
@login_required('supervisor')
def sup_defences():
    me = current_user()
    rows = query_db("""SELECT d.*, s.full_name AS student_name, s.matric_number,
                              t.title AS topic_title, t.title AS title
                       FROM defences d
                       JOIN users s ON s.id=d.student_id
                       JOIN topics t ON t.id=d.topic_id
                       WHERE d.supervisor_id=? ORDER BY
                       CASE d.status WHEN 'scheduled' THEN 0 ELSE 1 END,
                       d.scheduled_date""",
                    (me['id'],))
    # Eligible: approved topics with no defence yet
    eligible = query_db("""SELECT t.id AS topic_id, t.title, s.full_name AS student_name,
                                  s.matric_number,
                                  (SELECT COUNT(*) FROM chapters c
                                   WHERE c.topic_id=t.id AND c.status='approved') AS chaps_approved
                           FROM topics t
                           JOIN users s ON s.id=t.student_id
                           WHERE t.supervisor_id=? AND t.status='approved'
                             AND NOT EXISTS (SELECT 1 FROM defences d WHERE d.topic_id=t.id)
                           ORDER BY s.full_name""", (me['id'],))
    return render_template('supervisor/defences.html', defences=rows, eligible=eligible)


@app.route('/supervisor/defences/schedule/<int:tid>', methods=['GET', 'POST'])
@login_required('supervisor')
def sup_defence_schedule(tid):
    me = current_user()
    t = query_db("""SELECT t.*, s.full_name AS student_name, s.matric_number
                    FROM topics t JOIN users s ON s.id=t.student_id
                    WHERE t.id=? AND t.supervisor_id=? AND t.status='approved'""",
                 (tid, me['id']), one=True)
    if not t:
        abort(404)
    existing = query_db("SELECT * FROM defences WHERE topic_id=?", (tid,), one=True)
    if request.method == 'POST':
        f = request.form
        if existing:
            execute_db("""UPDATE defences SET scheduled_date=?, venue=?, status='scheduled' WHERE id=?""",
                       (f.get('scheduled_date'), f.get('venue'), existing['id']))
        else:
            execute_db("""INSERT INTO defences (topic_id,student_id,supervisor_id,scheduled_date,venue)
                          VALUES (?,?,?,?,?)""",
                       (tid, t['student_id'], me['id'],
                        f.get('scheduled_date'), f.get('venue')))
        notify(t['student_id'], 'Defence Scheduled',
               'Your project defence is set for ' + f.get('scheduled_date', '')
               + ' at ' + f.get('venue', ''),
               url_for('std_defence'), 'calendar')
        log_action('defence_schedule', 'topic:' + str(tid), '')
        flash('Defence scheduled.', 'success')
        return redirect(url_for('sup_defences'))
    return render_template('supervisor/defence_schedule.html', topic=t, existing=existing)


@app.route('/supervisor/defences/<int:did>/score', methods=['GET', 'POST'])
@login_required('supervisor')
def sup_defence_score(did):
    me = current_user()
    d = query_db("""SELECT d.*, s.full_name AS student_name, s.matric_number,
                           t.title AS topic_title
                    FROM defences d JOIN users s ON s.id=d.student_id
                    JOIN topics t ON t.id=d.topic_id
                    WHERE d.id=? AND d.supervisor_id=?""",
                 (did, me['id']), one=True)
    if not d:
        abort(404)
    if request.method == 'POST':
        f = request.form
        try:
            pres = max(0, min(30, int(f.get('presentation_score', 0))))
            proj = max(0, min(40, int(f.get('project_score', 0))))
            chap = max(0, min(30, int(f.get('chapters_score', 0))))
        except ValueError:
            flash('Scores must be integers.', 'danger')
            return redirect(url_for('sup_defence_score', did=did))
        total = pres + proj + chap
        gr = grade_from_score(total)
        execute_db("""UPDATE defences SET presentation_score=?, project_score=?,
                                          chapters_score=?, total_score=?, grade=?,
                                          panel_remarks=?, status='completed',
                                          completed_at=? WHERE id=?""",
                   (pres, proj, chap, total, gr, f.get('panel_remarks', ''),
                    datetime.now(), did))
        notify(d['student_id'], 'Defence Result Posted',
               'Final score: ' + str(total) + '/100  Grade: ' + gr,
               url_for('std_defence'), 'award')
        log_action('defence_score', 'defence:' + str(did),
                   'total=' + str(total) + ' grade=' + gr)
        flash('Score saved. Total ' + str(total) + '/100 (' + gr + ').', 'success')
        return redirect(url_for('sup_defences'))
    # auto-suggest chapters score = average of chapter grades * 30/100
    avg = query_db("""SELECT AVG(grade) g FROM chapters
                      WHERE topic_id=? AND grade IS NOT NULL""",
                   (d['topic_id'],), one=True)
    suggested = int((avg['g'] or 0) * 0.30) if avg else 0
    return render_template('supervisor/defence_score.html', defence=d, chapters_suggested=suggested)


@app.route('/supervisor/reports')
@login_required('supervisor')
def sup_reports():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    sid = cur['id'] if cur else 0
    stats = {
        'students':  query_db("""SELECT COUNT(*) c FROM allocations
                                 WHERE supervisor_id=? AND session_id=? AND status='active'""",
                              (me['id'], sid), one=True)['c'],
        'approved_topics': query_db("""SELECT COUNT(*) c FROM topics
                                       WHERE supervisor_id=? AND session_id=? AND status='approved'""",
                                    (me['id'], sid), one=True)['c'],
        'approved_chapters': query_db("""SELECT COUNT(*) c FROM chapters c JOIN topics t ON t.id=c.topic_id
                                         WHERE c.supervisor_id=? AND t.session_id=? AND c.status='approved'""",
                                      (me['id'], sid), one=True)['c'],
        'completed':  query_db("""SELECT COUNT(*) c FROM defences d JOIN topics t ON t.id=d.topic_id
                                  WHERE d.supervisor_id=? AND t.session_id=? AND d.status='completed'""",
                               (me['id'], sid), one=True)['c'],
    }
    rows = query_db("""
        SELECT s.full_name, s.matric_number,
               (SELECT title FROM topics WHERE student_id=s.id AND supervisor_id=? AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_title,
               (SELECT status FROM topics WHERE student_id=s.id AND supervisor_id=? AND session_id=? ORDER BY id DESC LIMIT 1) AS topic_status,
               (SELECT COUNT(DISTINCT chapter_no) FROM chapters c JOIN topics t ON t.id=c.topic_id
                WHERE c.student_id=s.id AND c.status='approved' AND t.session_id=?) AS approved_chapters,
               (SELECT total_score FROM defences d JOIN topics t ON t.id=d.topic_id
                WHERE d.student_id=s.id AND t.session_id=?) AS total_score,
               (SELECT grade FROM defences d JOIN topics t ON t.id=d.topic_id
                WHERE d.student_id=s.id AND t.session_id=?) AS grade
        FROM users s
        JOIN allocations a ON a.student_id=s.id
        WHERE a.supervisor_id=? AND a.session_id=? AND a.status='active' AND s.role='student'
        ORDER BY s.full_name
    """, (me['id'], sid, me['id'], sid, sid, sid, sid, me['id'], sid))
    return render_template('supervisor/reports.html', stats=stats, rows=rows,
                           session=cur, me=me)


# --------------------------------------------------------------------- #
# STUDENT ============================================================= #
# --------------------------------------------------------------------- #
def student_dashboard():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    sid = cur['id'] if cur else 0
    alloc = query_db("""SELECT a.*, sv.full_name AS sup_name, sv.title AS sup_title,
                               sv.email AS sup_email, sv.phone AS sup_phone
                        FROM allocations a JOIN users sv ON sv.id=a.supervisor_id
                        WHERE a.student_id=? AND a.session_id=? AND a.status='active'""",
                     (me['id'], sid), one=True)
    topic = query_db("""SELECT * FROM topics WHERE student_id=? AND session_id=?
                        ORDER BY id DESC LIMIT 1""",
                     (me['id'], sid), one=True)
    chapters = []
    if topic:
        chapters = query_db("""SELECT * FROM chapters WHERE topic_id=?
                               ORDER BY chapter_no, version DESC""", (topic['id'],))
    # latest version per chapter
    seen = set()
    latest = []
    for c in chapters:
        if c['chapter_no'] not in seen:
            latest.append(c)
            seen.add(c['chapter_no'])
    defence = None
    if topic:
        defence = query_db("SELECT * FROM defences WHERE topic_id=?",
                           (topic['id'],), one=True)
    progress = compute_progress(topic, latest, defence)
    announcements = query_db("""SELECT * FROM announcements
                                WHERE audience IN ('all','students')
                                ORDER BY created_at DESC LIMIT 3""")
    chapters_by_no = {}
    for c in latest:
        chapters_by_no[c['chapter_no']] = c
    return render_template('student/dashboard.html',
                           allocation=alloc, topic=topic,
                           chapters_by_no=chapters_by_no,
                           defence=defence, progress=progress,
                           announcements=announcements)


def compute_progress(topic, chapters, defence):
    """Return percent (0-100) plus a stage label."""
    if not topic:
        return {'pct': 5, 'stage': 'Awaiting topic submission'}
    if topic['status'] == 'pending':
        return {'pct': 15, 'stage': 'Topic awaiting approval'}
    if topic['status'] == 'rejected':
        return {'pct': 10, 'stage': 'Topic rejected — resubmit'}
    approved = sum(1 for c in chapters if c['status'] == 'approved')
    base = 20 + (approved * 12)  # max 80
    if defence and defence['status'] == 'completed':
        return {'pct': 100, 'stage': 'Defence completed'}
    if defence and defence['status'] == 'scheduled':
        return {'pct': min(90, base + 5), 'stage': 'Defence scheduled'}
    if approved == 5:
        return {'pct': 85, 'stage': 'All chapters approved — awaiting defence'}
    return {'pct': base, 'stage': 'Chapter ' + str(approved + 1) + ' in progress'}


@app.route('/student/topic', methods=['GET', 'POST'])
@login_required('student')
def std_topic():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    if not cur:
        flash('No active academic session.', 'warning')
        return redirect(url_for('dashboard'))
    alloc = query_db("""SELECT a.*, sv.full_name AS sup_name, sv.title AS sup_title
                        FROM allocations a JOIN users sv ON sv.id=a.supervisor_id
                        WHERE a.student_id=? AND a.session_id=? AND a.status='active'""",
                     (me['id'], cur['id']), one=True)
    if not alloc:
        flash('No supervisor has been allocated to you yet.', 'warning')
        return redirect(url_for('dashboard'))
    existing = query_db("""SELECT * FROM topics WHERE student_id=? AND session_id=?
                           ORDER BY id DESC LIMIT 1""",
                        (me['id'], cur['id']), one=True)
    history = query_db("""SELECT * FROM topics WHERE student_id=? AND session_id=?
                          ORDER BY created_at DESC""", (me['id'], cur['id']))
    if request.method == 'POST':
        if existing and existing['status'] == 'pending':
            flash('You already have a pending topic. Wait for supervisor decision.', 'warning')
            return redirect(url_for('std_topic'))
        if existing and existing['status'] == 'approved':
            flash('You already have an approved topic.', 'warning')
            return redirect(url_for('std_topic'))
        f = request.form
        submitted_title = f.get('title', '').strip()
        if not submitted_title:
            flash('Please enter a topic title before submitting.', 'warning')
            return redirect(url_for('std_topic'))
        conflict = find_topic_conflict(submitted_title, cur['id'],
                                       exclude_student_id=me['id'])
        if conflict:
            flash('This topic cannot be used. "' + conflict['title'] + '" has '
                  + ('already been approved' if conflict['status'] == 'approved'
                     else 'already been submitted')
                  + ' for another student in this session. Project topics must be '
                  'unique, so please change your title to a different, original '
                  'topic and submit again.', 'danger')
            return redirect(url_for('std_topic'))
        execute_db("""INSERT INTO topics (student_id,supervisor_id,session_id,title,
                                          background,objectives,methodology,submitted_by,status)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                   (me['id'], alloc['supervisor_id'], cur['id'],
                    f.get('title', '').strip(), f.get('background', '').strip(),
                    f.get('objectives', '').strip(), f.get('methodology', '').strip(),
                    'student', 'pending'))
        notify(alloc['supervisor_id'], 'New Topic for Review',
               me['full_name'] + ' submitted: ' + f.get('title', ''),
               url_for('sup_topics'), 'lightbulb')
        log_action('topic_submit', 'student:' + me['username'], f.get('title', ''))
        flash('Topic submitted for supervisor review.', 'success')
        return redirect(url_for('std_topic'))
    can_submit = not (existing and existing['status'] in ('pending', 'approved'))
    return render_template('student/topic.html',
                           allocation=alloc, alloc=alloc,
                           topics=history, history=history,
                           existing=existing, can_submit=can_submit)


@app.route('/student/chapters', methods=['GET', 'POST'])
@login_required('student')
def std_chapters():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    topic = query_db("""SELECT * FROM topics WHERE student_id=? AND session_id=?
                        AND status='approved' ORDER BY id DESC LIMIT 1""",
                     (me['id'], cur['id'] if cur else 0), one=True)
    if not topic:
        flash('You need an approved topic before uploading chapters.', 'warning')
        return redirect(url_for('std_topic'))
    if request.method == 'POST':
        try:
            chap_no = int(request.form.get('chapter_no', 0))
        except ValueError:
            chap_no = 0
        if chap_no not in (1, 2, 3, 4, 5):
            flash('Choose a valid chapter (1-5).', 'danger')
            return redirect(url_for('std_chapters'))
        # cannot re-submit approved chapter
        approved_exists = query_db("""SELECT 1 FROM chapters WHERE topic_id=? AND chapter_no=?
                                      AND status='approved'""",
                                   (topic['id'], chap_no), one=True)
        if approved_exists:
            flash('Chapter ' + str(chap_no) + ' is already approved.', 'warning')
            return redirect(url_for('std_chapters'))
        file = request.files.get('file')
        if not file or not file.filename:
            flash('Please attach the chapter file.', 'danger')
            return redirect(url_for('std_chapters'))
        if not allowed_file(file.filename):
            flash('Allowed formats: PDF, DOC, DOCX, ODT, TXT.', 'danger')
            return redirect(url_for('std_chapters'))
        fname = secure_filename(file.filename)
        token = uuid.uuid4().hex[:8]
        stored = 'ch' + str(chap_no) + '_' + str(me['id']) + '_' + token + '_' + fname
        path = os.path.join(UPLOAD_DIR, stored)
        file.save(path)
        # version
        last = query_db("""SELECT MAX(version) v FROM chapters
                           WHERE topic_id=? AND chapter_no=?""",
                        (topic['id'], chap_no), one=True)
        version = ((last['v'] or 0) + 1) if last else 1
        execute_db("""INSERT INTO chapters (topic_id,student_id,supervisor_id,chapter_no,
                                            title,file_path,original_name,notes,
                                            status,version)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (topic['id'], me['id'], topic['supervisor_id'], chap_no,
                    request.form.get('title', '').strip(), stored, fname,
                    request.form.get('notes', '').strip(), 'submitted', version))
        notify(topic['supervisor_id'], 'Chapter ' + str(chap_no) + ' submitted',
               me['full_name'] + ' uploaded chapter ' + str(chap_no)
               + (' (v' + str(version) + ')' if version > 1 else ''),
               url_for('sup_chapters'), 'upload')
        log_action('chapter_submit', 'topic:' + str(topic['id']),
                   'ch' + str(chap_no) + ' v' + str(version))
        flash('Chapter ' + str(chap_no) + ' uploaded (v' + str(version) + ').', 'success')
        return redirect(url_for('std_chapters'))
    rows = query_db("""SELECT * FROM chapters WHERE topic_id=?
                       ORDER BY chapter_no, version DESC""", (topic['id'],))
    by_chap = {}
    for r in rows:
        by_chap.setdefault(r['chapter_no'], []).append(r)
    return render_template('student/chapters.html', topic=topic, chapters_by_no=by_chap)


@app.route('/student/defence')
@login_required('student')
def std_defence():
    me = current_user()
    cur = query_db("SELECT * FROM sessions WHERE is_current=1 LIMIT 1", one=True)
    topic = query_db("""SELECT * FROM topics WHERE student_id=? AND session_id=?
                        AND status='approved' ORDER BY id DESC LIMIT 1""",
                     (me['id'], cur['id'] if cur else 0), one=True)
    defence = None
    if topic:
        defence = query_db("SELECT * FROM defences WHERE topic_id=?",
                           (topic['id'],), one=True)
    return render_template('student/defence.html', topic=topic, defence=defence)


# --------------------------------------------------------------------- #
# Shared routes ======================================================= #
# --------------------------------------------------------------------- #
@app.route('/notifications')
@login_required()
def notifications():
    me = current_user()
    rows = query_db("""SELECT * FROM notifications WHERE user_id=?
                       ORDER BY created_at DESC LIMIT 50""", (me['id'],))
    execute_db("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (me['id'],))
    return render_template('notifications.html', items=rows)


@app.route('/profile', methods=['GET', 'POST'])
@login_required()
def profile():
    me = current_user()
    if request.method == 'POST':
        f = request.form
        action = f.get('action', 'update_profile')
        if action == 'change_password':
            cur_pwd = f.get('current_password', '')
            new_pwd = f.get('new_password', '')
            conf_pwd = f.get('confirm_password', '')
            if not check_password_hash(me['password_hash'], cur_pwd):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('profile'))
            if len(new_pwd) < 6:
                flash('New password must be at least 6 characters.', 'danger')
                return redirect(url_for('profile'))
            if new_pwd != conf_pwd:
                flash('New passwords do not match.', 'danger')
                return redirect(url_for('profile'))
            execute_db("UPDATE users SET password_hash=?, force_pwd_change=0 WHERE id=?",
                       (generate_password_hash(new_pwd), me['id']))
            log_action('change_password', 'self', '')
            flash('Password updated successfully.', 'success')
        else:  # update_profile
            execute_db("""UPDATE users SET full_name=?, email=?, phone=?, title=? WHERE id=?""",
                       (f.get('full_name'), f.get('email'), f.get('phone'),
                        f.get('title'), me['id']))
            log_action('update_profile', 'self', '')
            flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html')


@app.route('/uploads/<path:fname>')
@login_required()
def serve_upload(fname):
    me = current_user()
    # only the student, their supervisor, or admin can download
    ch = query_db("SELECT * FROM chapters WHERE file_path=?", (fname,), one=True)
    if not ch:
        abort(404)
    if me['role'] == 'student' and ch['student_id'] != me['id']:
        abort(403)
    if me['role'] == 'supervisor' and ch['supervisor_id'] != me['id']:
        abort(403)
    return send_file(os.path.join(UPLOAD_DIR, fname),
                     as_attachment=True,
                     download_name=ch['original_name'] or fname)


@app.route('/announcements')
@login_required()
def view_announcements():
    me = current_user()
    aud = 'students' if me['role'] == 'student' else (
        'supervisors' if me['role'] == 'supervisor' else 'all')
    items = query_db("""SELECT a.*, u.full_name FROM announcements a
                        LEFT JOIN users u ON u.id=a.created_by
                        WHERE a.audience IN ('all', ?)
                        ORDER BY a.created_at DESC""", (aud,))
    return render_template('announcements.html', items=items)


# --------------------------------------------------------------------- #
# Error handlers
# --------------------------------------------------------------------- #
@app.errorhandler(404)
def e404(e):
    return render_template('error.html', code=404,
                           msg='The page you requested could not be found.'), 404


@app.errorhandler(403)
def e403(e):
    return render_template('error.html', code=403,
                           msg='You do not have permission to view this page.'), 403


@app.errorhandler(500)
def e500(e):
    return render_template('error.html', code=500,
                           msg='An internal error occurred.'), 500


# --------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------- #
if __name__ == '__main__':
    init_db()
    print('=' * 60)
    print(' UPAMS - Undergraduate Project Allocation & Management System')
    print(' Federal Polytechnic, Nasarawa')
    print('=' * 60)
    print(' Server:  http://127.0.0.1:5000')
    print(' Admin :  username=admin     password=admin123')
    print(' Sup.  :  username=jmebawondu password=super123')
    print(' Stu.  :  username=fpncs20240001 password=student123')
    print('=' * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)
