import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

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

def now_iso():
    return datetime.now(timezone.utc).isoformat()

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

def append_study(chat_id: str, user_id: str, username: str, topic: str, raw_text: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO study_log (ts, chat_id, user_id, username, topic, raw_text) VALUES (?,?,?,?,?,?)",
            (now_iso(), str(chat_id), str(user_id or ""), str(username or ""), topic, raw_text),
        )

def get_recent_study(chat_id: str, n: int = 5):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, topic FROM study_log WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), n),
        ).fetchall()
    return [dict(r) for r in rows]

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
