import os
import httpx
from fastapi import FastAPI, Request
from dotenv import load_dotenv

from db import get_due_item, get_next_item_anytime
from agent import send_recall_prompt

from db import (
    init_db,
    append_study,
    get_recent_study,
    get_random_study,
    set_mode,
    get_mode,
    append_resource_link,
)

# Quiz DB helpers (B)
from db import (
    ensure_quiz_tables,
    start_quiz,
    get_quiz,
    advance_quiz,
    end_quiz,
    log_quiz_result,
)

# Your existing rule-based agent helpers
from agent import (
    norm,
    is_help,
    is_recent,
    is_recollect,
    is_add_resource,
    is_cancel,
    extract_study_topic,
    extract_url,
)

# Quiz LLM helpers (B)
from agent_llm import llm_generate_quiz, llm_grade_answer

load_dotenv()

app = FastAPI()
init_db()
ensure_quiz_tables()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()  # optional
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# -------------------- Telegram Helpers --------------------

async def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})


async def tg_send_buttons(chat_id: str, text: str, buttons: list[list[dict]]):
    if not TELEGRAM_BOT_TOKEN:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)


async def tg_answer_callback(callback_query_id: str):
    if not TELEGRAM_BOT_TOKEN or not callback_query_id:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
        )


def allowed(user_id: str) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)


# -------------------- Buttons Menu --------------------

def main_menu_buttons():
    return [
        [
            {"text": "📝 Record study", "callback_data": "menu_record"},
            {"text": "📌 Recent", "callback_data": "menu_recent"},
        ],
        [
            {"text": "🧠 Recollect", "callback_data": "menu_recollect"},
            {"text": "🔁 Nudge me", "callback_data": "menu_nudge"},
        ],
        [
            {"text": "🎒 Add resource", "callback_data": "menu_add_resource"},
            {"text": "❓ Quiz me", "callback_data": "menu_quiz"},
        ],
        [
            {"text": "🗞 News", "callback_data": "menu_news"},
            {"text": "❌ Cancel", "callback_data": "menu_cancel"},
        ],
    ]


# -------------------- Health --------------------

@app.get("/")
async def health():
    return {"ok": True}


# -------------------- Main Webhook --------------------

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    # Always return {"ok": True} so Telegram doesn't retry spam.
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}

    # -------------------- CALLBACK QUERY (BUTTON PRESSES) --------------------
    cb = update.get("callback_query")
    if cb:
        cb_id = cb.get("id", "")
        msg_obj = cb.get("message") or {}
        chat_id = str((msg_obj.get("chat") or {}).get("id", ""))
        user = cb.get("from") or {}
        user_id = str(user.get("id", ""))
        data = cb.get("data", "")

        # acknowledge callback so Telegram UI feels responsive
        await tg_answer_callback(cb_id)

        if not chat_id or not allowed(user_id):
            return {"ok": True}

        # Menu actions
        if data == "menu_recent":
            items = get_recent_study(chat_id, n=5)
            if not items:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            else:
                lines = [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
                await tg_send(chat_id, "📌 Recent study:\n" + "\n".join(lines))

        elif data == "menu_recollect":
            item = get_random_study(chat_id)
            if not item:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            else:
                await tg_send(chat_id, f"🧠 Recollect:\n{item['topic']}\n({item['ts']})")

        elif data == "menu_record":
            set_mode(chat_id, "awaiting_study")
            await tg_send(chat_id, '📝 Send: "I studied ..." OR just type the topic.')

        elif data == "menu_add_resource":
            set_mode(chat_id, "awaiting_resource")
            await tg_send(chat_id, "🎒 Paste a link to save (or type cancel).")

        elif data == "menu_quiz":
            set_mode(chat_id, "awaiting_quiz_topic")
            await tg_send(chat_id, "❓ What topic should I quiz you on?\n\nType a topic OR send: quiz recent")

        elif data == "menu_nudge":
            item = get_due_item(chat_id) or get_next_item_anytime(chat_id)
            if not item:
                await tg_send(chat_id, 'No recall items yet. Save something with: "I studied ..."')
            else:
                await send_recall_prompt(chat_id, item, tg_send)

        elif data == "menu_news":
            await tg_send(chat_id, "🗞 News agent coming next.")

        elif data == "menu_cancel":
            set_mode(chat_id, "")
            end_quiz(chat_id)  # safe even if no session
            await tg_send_buttons(chat_id, "✅ Cancelled.", main_menu_buttons())

        return {"ok": True}

    # -------------------- MESSAGE (TEXT INPUT) --------------------
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = str((msg.get("chat") or {}).get("id", ""))
    user = msg.get("from") or {}
    user_id = str(user.get("id", ""))
    username = user.get("username", "") or ""
    text = norm(msg.get("text", ""))

    if not chat_id or not allowed(user_id):
        return {"ok": True}

    # Global commands
    if is_help(text):
        await tg_send_buttons(chat_id, "Choose an action:", main_menu_buttons())
        return {"ok": True}

    if is_cancel(text):
        set_mode(chat_id, "")
        end_quiz(chat_id)
        await tg_send_buttons(chat_id, "✅ Cancelled.", main_menu_buttons())
        return {"ok": True}

    # Mode-based handling
    mode = get_mode(chat_id)

    # -------------------- (A) STUDY RECORD FLOW --------------------
    if mode == "awaiting_study" and text:
        topic = extract_study_topic(text) or text
        append_study(chat_id, user_id, username, topic, text)
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, f'✅ Saved. You studied: "{topic}"', main_menu_buttons())
        return {"ok": True}

    # -------------------- (A) RESOURCE SAVE FLOW --------------------
    if mode == "awaiting_resource":
        url = extract_url(text)
        if not url:
            await tg_send(chat_id, "Paste a URL (or type cancel).")
            return {"ok": True}
        append_resource_link(chat_id, user_id, title="Saved link", url=url, raw_text=text)
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, f"🔖 Saved to Learning Bag:\n{url}", main_menu_buttons())
        return {"ok": True}

    # -------------------- (B) QUIZ FLOW: TOPIC SELECTION --------------------
    if mode == "awaiting_quiz_topic":
        if text == "quiz recent":
            items = get_recent_study(chat_id, n=1)
            if not items:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
                return {"ok": True}
            topic = items[0]["topic"]
        else:
            topic = text.strip()

        questions = await llm_generate_quiz(topic, n=5)
        start_quiz(chat_id, topic, questions)
        set_mode(chat_id, "awaiting_quiz_answer")

        q0 = questions[0]["q"]
        await tg_send(chat_id, f"🧪 Quiz started: {topic}\n\nQ1) {q0}\n\nReply with your answer.")
        return {"ok": True}

    # -------------------- (B) QUIZ FLOW: ANSWER -> GRADE -> NEXT --------------------
    if mode == "awaiting_quiz_answer":
        session = get_quiz(chat_id)
        if not session:
            set_mode(chat_id, "")
            await tg_send(chat_id, "Quiz session not found. Tap Quiz me again.")
            return {"ok": True}

        topic = session["topic"]
        questions = session["questions"]
        idx = session["idx"]

        if idx >= len(questions):
            end_quiz(chat_id)
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, "✅ Quiz complete.", main_menu_buttons())
            return {"ok": True}

        q = questions[idx]["q"]
        ideal = questions[idx].get("ideal", "")
        user_answer = text

        graded = await llm_grade_answer(topic, q, ideal, user_answer)
        score = int(graded.get("score", 0))
        verdict = graded.get("verdict", "")

        log_quiz_result(chat_id, topic, q, ideal, user_answer, score, verdict)

        good = "\n".join([f"• {x}" for x in graded.get("what_was_good", [])][:3]) or "• —"
        improve = "\n".join([f"• {x}" for x in graded.get("what_to_improve", [])][:3]) or "• —"
        model_ans = graded.get("model_answer", "") or ideal

        await tg_send(
            chat_id,
            f"🧾 Grading (Q{idx+1}) — Score: {score}/10\n{verdict}\n\n✅ What you did well:\n{good}\n\n🔧 Improve:\n{improve}\n\n⭐ Better answer:\n{model_ans}"
        )

        advance_quiz(chat_id)
        session2 = get_quiz(chat_id)
        idx2 = session2["idx"]

        if idx2 >= len(session2["questions"]):
            end_quiz(chat_id)
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, "✅ Quiz finished. Want another?", main_menu_buttons())
            return {"ok": True}

        next_q = session2["questions"][idx2]["q"]
        await tg_send(chat_id, f"Q{idx2+1}) {next_q}\n\nReply with your answer (or type cancel).")
        return {"ok": True}

    # -------------------- Text command fallbacks --------------------

    if is_recent(text):
        items = get_recent_study(chat_id, n=5)
        if not items:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
        else:
            lines = [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
            await tg_send(chat_id, "📌 Recent study:\n" + "\n".join(lines))
        return {"ok": True}

    if is_recollect(text):
        item = get_random_study(chat_id)
        if not item:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
        else:
            await tg_send(chat_id, f"🧠 Recollect:\n{item['topic']}\n({item['ts']})")
        return {"ok": True}

    # Inline study ingestion (no button needed)
    topic = extract_study_topic(text)
    if topic:
        append_study(chat_id, user_id, username, topic, text)
        await tg_send(chat_id, f'✅ Saved. You studied: "{topic}"')
        return {"ok": True}

    # Default fallback
    await tg_send(chat_id, 'Got it. Type "help" for commands.')
    return {"ok": True}
