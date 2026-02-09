import sqlite3
from datetime import datetime, timezone
import json
import os

DB_PATH = os.getenv("DB_PATH", "anamnesis.db")

def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    conn = _conn()
    cur = conn.cursor()

    # Core Study Tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        user_id TEXT,
        username TEXT,
        topic TEXT,
        raw_text TEXT,
        ts TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS resource_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        user_id TEXT,
        title TEXT,
        url TEXT,
        raw_text TEXT,
        ts TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_modes (
        chat_id TEXT PRIMARY KEY,
        mode TEXT
    )""")

    # Quiz Tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chat_id TEXT NOT NULL,
      user_id TEXT,
      topic TEXT NOT NULL,
      created_ts TEXT NOT NULL,
      status TEXT NOT NULL,
      current_idx INTEGER NOT NULL DEFAULT 0,
      score INTEGER NOT NULL DEFAULT 0,
      total INTEGER NOT NULL DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_questions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER NOT NULL,
      q_idx INTEGER NOT NULL,
      question TEXT NOT NULL,
      a TEXT NOT NULL,
      b TEXT NOT NULL,
      c TEXT NOT NULL,
      d TEXT NOT NULL,
      correct TEXT NOT NULL,
      explanation TEXT NOT NULL,
      user_answer TEXT,
      FOREIGN KEY(session_id) REFERENCES quiz_sessions(id)
    )""")

    conn.commit()
    conn.close()

# --- Mode Management ---
def set_mode(chat_id, mode):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO user_modes (chat_id, mode) VALUES (?, ?)", (str(chat_id), mode))
    conn.commit()
    conn.close()

def get_mode(chat_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT mode FROM user_modes WHERE chat_id=?", (str(chat_id),))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else ""

# --- Study & Resource Functions ---
def append_study(chat_id, user_id, username, topic, raw_text):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO study_logs (chat_id, user_id, username, topic, raw_text, ts) VALUES (?,?,?,?,?,?)",
                (str(chat_id), str(user_id), username, topic, raw_text, _now_iso()))
    conn.commit()
    conn.close()

def get_recent_study(chat_id, n=5):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT topic, ts FROM study_logs WHERE chat_id=? ORDER BY id DESC LIMIT ?", (str(chat_id), n))
    rows = cur.fetchall()
    conn.close()
    return [{"topic": r[0], "ts": r[1]} for r in rows]

def get_random_study(chat_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT topic, ts FROM study_logs WHERE chat_id=? ORDER BY RANDOM() LIMIT 1", (str(chat_id),))
    row = cur.fetchone()
    conn.close()
    return {"topic": row[0], "ts": row[1]} if row else None

def append_resource_link(chat_id, user_id, title, url, raw_text):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO resource_links (chat_id, user_id, title, url, raw_text, ts) VALUES (?,?,?,?,?,?)",
                (str(chat_id), str(user_id), title, url, raw_text, _now_iso()))
    conn.commit()
    conn.close()

# --- Quiz Session Logic ---
def create_quiz_session(chat_id: str, user_id: str, topic: str, questions: list[dict]) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO quiz_sessions (chat_id, user_id, topic, created_ts, status, current_idx, score, total)
      VALUES (?, ?, ?, ?, 'active', 0, 0, ?)
    """, (str(chat_id), str(user_id), topic, _now_iso(), len(questions)))
    session_id = cur.lastrowid
    for i, q in enumerate(questions):
        cur.execute("""
          INSERT INTO quiz_questions (session_id, q_idx, question, a, b, c, d, correct, explanation)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, i, q["question"], q["A"], q["B"], q["C"], q["D"], q["correct"], q.get("explanation", "")))
    conn.commit()
    conn.close()
    return session_id

def get_active_quiz_session(chat_id: str):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id, topic, current_idx, score, total FROM quiz_sessions WHERE chat_id=? AND status='active' ORDER BY id DESC LIMIT 1", (str(chat_id),))
    row = cur.fetchone()
    conn.close()
    return {"id": row[0], "topic": row[1], "current_idx": row[2], "score": row[3], "total": row[4]} if row else None

def get_quiz_question(session_id: int, q_idx: int):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT question, a, b, c, d, correct, explanation, user_answer FROM quiz_questions WHERE session_id=? AND q_idx=? LIMIT 1", (session_id, q_idx))
    row = cur.fetchone()
    conn.close()
    return {"question": row[0], "A": row[1], "B": row[2], "C": row[3], "D": row[4], "correct": row[5], "explanation": row[6], "user_answer": row[7]} if row else None

def answer_quiz_question(session_id: int, q_idx: int, answer: str) -> dict:
    answer = (answer or "").strip().upper()
    if answer not in ["A", "B", "C", "D"]: return {"error": "Invalid answer."}
    q = get_quiz_question(session_id, q_idx)
    if not q: return {"error": "Question not found."}
    if q["user_answer"]: return {"error": "Already answered."}
    
    conn = _conn()
    cur = conn.cursor()
    is_correct = (answer == q["correct"])
    cur.execute("UPDATE quiz_questions SET user_answer=? WHERE session_id=? AND q_idx=?", (answer, session_id, q_idx))
    cur.execute("SELECT score, total, current_idx FROM quiz_sessions WHERE id=?", (session_id,))
    score, total, current_idx = cur.fetchone()
    
    if is_correct: score += 1
    next_idx = current_idx + 1
    done = (next_idx >= total)
    
    status = 'done' if done else 'active'
    cur.execute("UPDATE quiz_sessions SET score=?, current_idx=?, status=? WHERE id=?", (score, next_idx, status, session_id))
    
    conn.commit()
    conn.close()
    return {"is_correct": is_correct, "correct": q["correct"], "explanation": q["explanation"], "new_score": score, "done": done, "next_idx": next_idx, "total": total}

def create_quiz_session(chat_id: str, topic: str):
    # placeholder for future quiz-session persistence
    return {"chat_id": chat_id, "topic": topic}


# Placeholder for nudge logic
def get_due_item(chat_id): return None
def get_next_item_anytime(chat_id): return get_random_study(chat_id)
