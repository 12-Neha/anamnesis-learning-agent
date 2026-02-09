import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DB_PATH = os.getenv("DB_PATH", "./data.db")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def now_utc():
    return datetime.now(timezone.utc)

def now_iso():
    return now_utc().isoformat()

def iso_in_days(days: int):
    return (now_utc() + timedelta(days=days)).isoformat()

def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS study_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            user_id TEXT,
            username TEXT,
            topic TEXT NOT NULL,
            raw_text TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            user_id TEXT,
            type TEXT NOT NULL,
            title TEXT,
            url TEXT,
            notes TEXT,
            raw_text TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            chat_id TEXT PRIMARY KEY,
            mode TEXT,
            updated_at TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS state_kv (
            chat_id TEXT NOT NULL,
            k TEXT NOT NULL,
            v TEXT,
            updated_at TEXT,
            PRIMARY KEY (chat_id, k)
        );
        """)

        # ✅ Spaced repetition queue
        conn.execute("""
        CREATE TABLE IF NOT EXISTS review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            study_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 1,
            due_ts TEXT NOT NULL,
            last_result TEXT DEFAULT "",
            created_ts TEXT NOT NULL,
            updated_ts TEXT NOT NULL
        );
        """)

def append_study(chat_id: str, user_id: str, username: str, topic: str, raw_text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO study_log (ts, chat_id, user_id, username, topic, raw_text) VALUES (?,?,?,?,?,?)",
            (now_iso(), str(chat_id), str(user_id or ""), str(username or ""), topic, raw_text),
        )
        return int(cur.lastrowid)

def enqueue_review(chat_id: str, study_id: int, topic: str):
    # First review due tomorrow (simple default)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO review_queue (chat_id, study_id, topic, interval_days, due_ts, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
            (str(chat_id), int(study_id), topic, 1, iso_in_days(1), now_iso(), now_iso()),
        )

def get_recent_study(chat_id: str, n: int = 5):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, topic FROM study_log WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), n),
        ).fetchall()
    return [dict(r) for r in rows]

def get_most_recent_topic(chat_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT topic FROM study_log WHERE chat_id=? ORDER BY id DESC LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    return (row["topic"] if row else None)

def get_random_study(chat_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts, topic FROM study_log WHERE chat_id=? ORDER BY RANDOM() LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    return dict(row) if row else None

def set_mode(chat_id: str, mode: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO state (chat_id, mode, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at",
            (str(chat_id), mode, now_iso()),
        )

def get_mode(chat_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT mode FROM state WHERE chat_id=?", (str(chat_id),)).fetchone()
    return (row["mode"] if row else "") or ""

def append_resource_link(chat_id: str, user_id: str, title: str, url: str, raw_text: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO resources (ts, chat_id, user_id, type, title, url, notes, raw_text) VALUES (?,?,?,?,?,?,?,?)",
            (now_iso(), str(chat_id), str(user_id or ""), "link", title, url, "", raw_text),
        )

# -------- KV helpers --------
def kv_set(chat_id: str, k: str, v: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO state_kv (chat_id, k, v, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(chat_id, k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
            (str(chat_id), k, v, now_iso()),
        )

def kv_get(chat_id: str, k: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT v FROM state_kv WHERE chat_id=? AND k=?",
            (str(chat_id), k),
        ).fetchone()
    return row["v"] if row else None

def kv_del(chat_id: str, k: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM state_kv WHERE chat_id=? AND k=?",
            (str(chat_id), k),
        )

# -------- Spaced repetition helpers --------
def get_due_item(chat_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE chat_id=? AND due_ts <= ? ORDER BY due_ts ASC LIMIT 1",
            (str(chat_id), now_iso()),
        ).fetchone()
    return dict(row) if row else None

def get_next_item_anytime(chat_id: str):
    # if nothing due yet, pick the soonest upcoming (so "nudge" always returns something)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE chat_id=? ORDER BY due_ts ASC LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    return dict(row) if row else None

def update_review_result(chat_id: str, review_id: int, remembered: bool):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT interval_days FROM review_queue WHERE id=? AND chat_id=?",
            (int(review_id), str(chat_id)),
        ).fetchone()
        if not row:
            return

        interval = int(row["interval_days"] or 1)
        if remembered:
            new_interval = min(interval * 2, 30)   # cap at 30 days
            result = "remembered"
        else:
            new_interval = 1
            result = "forgot"

        conn.execute(
            "UPDATE review_queue SET interval_days=?, due_ts=?, last_result=?, updated_ts=? WHERE id=? AND chat_id=?",
            (new_interval, iso_in_days(new_interval), result, now_iso(), int(review_id), str(chat_id)),
        )

# ----------------- QUIZ SESSION (B) -----------------

import json
from datetime import datetime, timezone

def ensure_quiz_tables():
    con = _conn()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_sessions (
        chat_id TEXT PRIMARY KEY,
        topic TEXT,
        questions_json TEXT,
        idx INTEGER DEFAULT 0,
        started_ts TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        topic TEXT,
        question TEXT,
        ideal TEXT,
        answer TEXT,
        score INTEGER,
        verdict TEXT,
        created_ts TEXT
    )
    """)
    con.commit()
    con.close()

def start_quiz(chat_id: str, topic: str, questions: list[dict]):
    ensure_quiz_tables()
    con = _conn()
    cur = con.cursor()
    cur.execute("""
      INSERT INTO quiz_sessions(chat_id, topic, questions_json, idx, started_ts)
      VALUES(?,?,?,?,?)
      ON CONFLICT(chat_id) DO UPDATE SET
        topic=excluded.topic,
        questions_json=excluded.questions_json,
        idx=0,
        started_ts=excluded.started_ts
    """, (chat_id, topic, json.dumps(questions), 0, datetime.now(timezone.utc).isoformat()))
    con.commit()
    con.close()

def get_quiz(chat_id: str):
    ensure_quiz_tables()
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT topic, questions_json, idx FROM quiz_sessions WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    topic, qj, idx = row
    return {"topic": topic, "questions": json.loads(qj), "idx": idx}

def advance_quiz(chat_id: str):
    con = _conn()
    cur = con.cursor()
    cur.execute("UPDATE quiz_sessions SET idx = idx + 1 WHERE chat_id=?", (chat_id,))
    con.commit()
    con.close()

def end_quiz(chat_id: str):
    con = _conn()
    cur = con.cursor()
    cur.execute("DELETE FROM quiz_sessions WHERE chat_id=?", (chat_id,))
    con.commit()
    con.close()

def log_quiz_result(chat_id: str, topic: str, question: str, ideal: str, answer: str, score: int, verdict: str):
    ensure_quiz_tables()
    con = _conn()
    cur = con.cursor()
    cur.execute("""
      INSERT INTO quiz_logs(chat_id, topic, question, ideal, answer, score, verdict, created_ts)
      VALUES(?,?,?,?,?,?,?,?)
    """, (chat_id, topic, question, ideal, answer, score, verdict, datetime.now(timezone.utc).isoformat()))
    con.commit()
    con.close()
