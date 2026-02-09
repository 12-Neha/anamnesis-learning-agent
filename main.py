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
    append_resource_link,
    get_due_item,
    get_next_item_anytime,
    create_quiz_session,
    get_active_quiz_session,
    get_quiz_question,
    answer_quiz_question
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
    HELP_TEXT,
    generate_quiz_questions,
    send_recall_prompt,
)

load_dotenv()
app = FastAPI()
init_db()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
VERCEL_ORIGIN = os.getenv("VERCEL_ORIGIN", "*").strip()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[VERCEL_ORIGIN] if VERCEL_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def allowed(user_id: str) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)

async def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN: return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

async def tg_send_buttons(chat_id: str, text: str, buttons: list[list[dict]]):
    if not TELEGRAM_BOT_TOKEN: return
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": buttons}, "parse_mode": "Markdown"}
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def main_menu_buttons():
    return [
        [{"text": "📝 Record study", "callback_data": "menu_record"},
         {"text": "📌 Recent", "callback_data": "menu_recent"}],
        [{"text": "🧠 Recollect", "bullet": "menu_recollect"},
         {"text": "🎒 Add resource", "callback_data": "menu_add_resource"}],
        [{"text": "❓ Quiz Me", "callback_data": "menu_quiz_start"}],
        [{"text": "🔁 Nudge me", "callback_data": "menu_nudge"},
         {"text": "❌ Cancel", "callback_data": "menu_cancel"}],
    ]

@app.get("/")
async def health():
    return {"ok": True}

# --- Telegram Webhook Logic ---

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    try:
        update = await req.json()
    except:
        return {"ok": True}

    cb = update.get("callback_query")
    if cb:
        chat_id = str(cb["message"]["chat"]["id"])
        user_id = str(cb["from"]["id"])
        data = cb.get("data", "")
        if not allowed(user_id): return {"ok": True}

        # Quiz Start Logic
        if data == "menu_quiz_start":
            recent = get_recent_study(chat_id, n=1)
            if not recent:
                await tg_send(chat_id, "No study history found. Record something first!")
            else:
                topic = recent[0]["topic"]
                questions = generate_quiz_questions(topic)
                create_quiz_session(chat_id, user_id, topic, questions)
                await tg_send(chat_id, f"🚀 Starting quiz on: *{topic}*")
                q = get_quiz_question(get_active_quiz_session(chat_id)["id"], 0)
                buttons = [[{"text": opt, "callback_data": f"quiz_ans_{opt}"}] for opt in ["A", "B", "C", "D"]]
                await tg_send_buttons(chat_id, f"1/5: {q['question']}\n\nA) {q['A']}\nB) {q['B']}\nC) {q['C']}\nD) {q['D']}", buttons)

        # Quiz Answer Logic
        elif data.startswith("quiz_ans_"):
            ans = data.replace("quiz_ans_", "")
            session = get_active_quiz_session(chat_id)
            if session:
                res = answer_quiz_question(session["id"], session["current_idx"], ans)
                feedback = "✅ Correct!" if res["is_correct"] else f"❌ Wrong. Correct: {res['correct']}"
                await tg_send(chat_id, f"{feedback}\n\n_{res['explanation']}_")
                
                if res["done"]:
                    await tg_send_buttons(chat_id, f"🏆 Quiz Finished!\nScore: {res['new_score']}/{res['total']}", main_menu_buttons())
                else:
                    next_q = get_quiz_question(session["id"], res["next_idx"])
                    buttons = [[{"text": opt, "callback_data": f"quiz_ans_{opt}"}] for opt in ["A", "B", "C", "D"]]
                    await tg_send_buttons(chat_id, f"{res['next_idx']+1}/{res['total']}: {next_q['question']}\n\nA) {next_q['A']}\nB) {next_q['B']}\nC) {next_q['C']}\nD) {next_q['D']}", buttons)

        # Standard Menu Handlers
        elif data == "menu_record":
            set_mode(chat_id, "awaiting_study")
            await tg_send(chat_id, "📝 What are you studying?")
        elif data == "menu_cancel":
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, "✅ Cancelled.", main_menu_buttons())

        async with httpx.AsyncClient() as client:
            await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
        return {"ok": True}

    # Handle text messages
    msg = update.get("message")
    if not msg: return {"ok": True}
    chat_id, text = str(msg["chat"]["id"]), norm(msg.get("text", ""))
    user_id = str(msg["from"]["id"])
    if not allowed(user_id): return {"ok": True}

    if is_help(text):
        await tg_send_buttons(chat_id, "Choose an action:", main_menu_buttons())
    elif is_cancel(text):
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, "Cancelled.", main_menu_buttons())
    elif get_mode(chat_id) == "awaiting_study" and text:
        append_study(chat_id, user_id, msg["from"].get("username", "user"), text, text)
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, f"✅ Recorded: {text}", main_menu_buttons())
    else:
        await tg_send(chat_id, "Use the menu to get started!")

    return {"ok": True}
