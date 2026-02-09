import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from db import (
    init_db,
    append_study,
    get_recent_study,
    get_random_study,
    set_mode,
    get_mode,
    append_resource_link
)

from agent import (
    norm,
    is_help,
    is_recent,
    is_recollect,
    is_add_resource,
    is_cancel,
    extract_study_topic,
    extract_url,
    HELP_TEXT
)


load_dotenv()
app = FastAPI()
init_db()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
VERCEL_ORIGIN = os.getenv("VERCEL_ORIGIN", "*").strip()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def allowed(user_id: str) -> bool:
    return not ALLOWED_USER_ID or str(user_id) == str(ALLOWED_USER_ID)

async def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN: return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

async def tg_send_buttons(chat_id: str, text: str, buttons: list[list[dict]]):
    if not TELEGRAM_BOT_TOKEN: return
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": buttons}}
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

# --- QUIZ HELPERS ---

def quiz_answer_buttons(session_id: int):
    return [[
      {"text": "A", "callback_data": f"quiz_ans:{session_id}:A"},
      {"text": "B", "callback_data": f"quiz_ans:{session_id}:B"},
      {"text": "C", "callback_data": f"quiz_ans:{session_id}:C"},
      {"text": "D", "callback_data": f"quiz_ans:{session_id}:D"},
    ]]

def format_quiz_q(q: dict, idx: int, total: int, topic: str):
    return (
      f"❓ Quiz on: {topic}\n"
      f"Q{idx+1}/{total}: {q['question']}\n\n"
      f"A) {q['A']}\n"
      f"B) {q['B']}\n"
      f"C) {q['C']}\n"
      f"D) {q['D']}"
    )

def main_menu_buttons():
    return [
        [{"text": "📝 Record study", "callback_data": "menu_record"},
         {"text": "📌 Recent", "callback_data": "menu_recent"}],
        [{"text": "🧠 Recollect", "callback_data": "menu_recollect"},
         {"text": "🎒 Add resource", "callback_data": "menu_add_resource"}],
        [{"text": "🔁 Nudge me", "callback_data": "menu_nudge"},
         {"text": "❓ Quiz me", "callback_data": "menu_quiz"}],
        [{"text": "❌ Cancel", "callback_data": "menu_cancel"}],
    ]

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    try:
        update = await req.json()
    except:
        return {"ok": True}

    cb = update.get("callback_query")
    async with httpx.AsyncClient(timeout=10) as client:
        if cb:
            chat_id = str(cb["message"]["chat"]["id"])
            user_id = str(cb["from"]["id"])
            data = cb.get("data", "")
            if not allowed(user_id): return {"ok": True}

            # --- QUIZ ANSWERS (callback) ---
            if data.startswith("quiz_ans:"):
                parts = data.split(":")
                session_id, ans = int(parts[1]), parts[2].strip().upper()
                sess = get_active_quiz_session(chat_id)

                if not sess or sess["id"] != session_id:
                    await tg_send(chat_id, "This quiz session is not active anymore. Type: quiz me")
                    await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb.get("id")})
                    return {"ok": True}

                result = answer_quiz_question(session_id, sess["current_idx"], ans)
                if "error" in result:
                    await tg_send(chat_id, f"⚠️ {result['error']}")
                else:
                    verdict = "✅ Correct!" if result["is_correct"] else f"❌ Not quite. Correct: {result['correct']}"
                    await tg_send(chat_id, f"{verdict}\n{result['explanation']}\n\nScore: {result['new_score']}/{result['total']}")

                    if result["done"]:
                        await tg_send_buttons(chat_id, "🎉 Quiz complete! Want to do another?", main_menu_buttons())
                    else:
                        updated = get_active_quiz_session(chat_id)
                        q = get_quiz_question(session_id, updated["current_idx"])
                        msg_txt = format_quiz_q(q, updated["current_idx"], updated["total"], updated["topic"])
                        await tg_send_buttons(chat_id, msg_txt, quiz_answer_buttons(session_id))

                await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb.get("id")})
                return {"ok": True}

            elif data == "menu_quiz":
                set_mode(chat_id, "awaiting_quiz_topic")
                await tg_send(chat_id, "❓ What topic should I quiz you on?\nType a topic OR send: quiz recent")
            elif data == "menu_record":
                set_mode(chat_id, "awaiting_study")
                await tg_send(chat_id, "📝 Send the topic you just studied.")
            elif data == "menu_cancel":
                set_mode(chat_id, "")
                await tg_send(chat_id, "✅ Cancelled.")

            await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb.get("id")})
            return {"ok": True}

    msg = update.get("message")
    if not msg: return {"ok": True}
    chat_id, user_id, text = str(msg["chat"]["id"]), str(msg["from"]["id"]), msg.get("text", "")
    if not allowed(user_id): return {"ok": True}

    mode = get_mode(chat_id)
   

    

    # --- QUIZ TOPIC MODE (SAFE PLACEHOLDER) ---
    if mode == "awaiting_quiz_topic" and text:
        if text.strip().lower() == "quiz recent":
            rec = get_recent_study(chat_id, n=1)
            if not rec:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
                return {"ok": True}
            topic = rec[0]["topic"]
        else:
            topic = text.strip()

        set_mode(chat_id, "")
        await tg_send(
            chat_id,
            f"🧠 Quiz coming soon for: {topic}\n\nFor now, tell me:\n1. Key idea\n2. Example\n3. When you'd use it"
        )
        await tg_send_buttons(chat_id, "Main Menu:", main_menu_buttons())
        return {"ok": True}


@app.get("/")
async def health():
    return {"ok": True}