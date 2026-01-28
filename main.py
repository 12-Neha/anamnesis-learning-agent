import os
import json
import httpx
from fastapi import FastAPI, Request
from dotenv import load_dotenv

from db import (
    init_db, append_study, get_recent_study, get_most_recent_topic, get_random_study,
    set_mode, get_mode, append_resource_link,
    kv_set, kv_get, kv_del
)
from agent import (
    norm, is_help, is_recent, is_recollect, is_add_resource, is_cancel,
    extract_study_topic, extract_url, HELP_TEXT
)

load_dotenv()
app = FastAPI()
init_db()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def tg_send_buttons(chat_id: str, text: str, buttons: list[list[dict]]):
    if not TELEGRAM_BOT_TOKEN:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)


def main_menu_buttons():
    return [
        [{"text": "📝 Record study", "callback_data": "menu_record"},
         {"text": "📌 Recent", "callback_data": "menu_recent"}],
        [{"text": "🧠 Recollect", "callback_data": "menu_recollect"},
         {"text": "🎒 Add resource", "callback_data": "menu_add_resource"}],
        [{"text": "❓ Quiz me", "callback_data": "menu_quiz"},
         {"text": "🗞 News", "callback_data": "menu_news"}],
        [{"text": "❌ Cancel", "callback_data": "menu_cancel"}],
    ]


def allowed(user_id: str) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)


def build_quiz_questions(topic: str):
    return [
        f"1) Define **{topic}** in 2–3 lines.",
        f"2) Give one real example where **{topic}** applies.",
        f"3) What’s a common mistake or misconception about **{topic}**?",
    ]


@app.get("/")
async def health():
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}

    # --- Button clicks ---
    cb = update.get("callback_query")
    if cb:
        chat_id = str((cb.get("message") or {}).get("chat", {}).get("id", ""))
        user = cb.get("from") or {}
        user_id = str(user.get("id", ""))
        data = cb.get("data", "")

        if not chat_id or not allowed(user_id):
            return {"ok": True}

        if data == "menu_recent":
            items = get_recent_study(chat_id, n=5)
            if not items:
                out = "No study items yet. Try: I studied EOQ"
            else:
                out = "📌 Recent study:\n" + "\n".join(
                    [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
                )
            await tg_send(chat_id, out)

        elif data == "menu_recollect":
            item = get_random_study(chat_id)
            out = 'No study items yet. Try: "I studied EOQ"' if not item else f"🧠 Recollect:\n{item['topic']}\n({item['ts']})"
            await tg_send(chat_id, out)

        elif data == "menu_add_resource":
            set_mode(chat_id, "awaiting_resource")
            await tg_send(chat_id, "🎒 Paste a link to save (or type cancel).")

        elif data == "menu_record":
            set_mode(chat_id, "awaiting_study")
            await tg_send(chat_id, '📝 Send: "I studied ..." or just type the topic.')

        elif data == "menu_cancel":
            set_mode(chat_id, "")
            kv_del(chat_id, "quiz_questions")
            kv_del(chat_id, "quiz_answers")
            await tg_send(chat_id, "✅ Cancelled.")

        elif data == "menu_quiz":
            set_mode(chat_id, "awaiting_quiz_topic")
            await tg_send(chat_id, "❓ What topic should I quiz you on?\n\nType a topic OR send: `quiz recent`")

        elif data == "menu_news":
            await tg_send(chat_id, "🗞 News agent coming next.")

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": cb.get("id")},
            )
        return {"ok": True}

    # --- Normal messages ---
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

    if is_help(text):
        await tg_send_buttons(chat_id, "Choose an action:", main_menu_buttons())
        return {"ok": True}

    if is_cancel(text):
        set_mode(chat_id, "")
        kv_del(chat_id, "quiz_questions")
        kv_del(chat_id, "quiz_answers")
        await tg_send(chat_id, "✅ Cancelled.")
        return {"ok": True}

    # --- Mode-based Handling ---
    mode = get_mode(chat_id)

    if mode == "awaiting_study" and text:
        topic = extract_study_topic(text) or text
        append_study(chat_id, user_id, username, topic, text)
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, f'✅ Saved: "{topic}"', main_menu_buttons())
        return {"ok": True}

    if mode == "awaiting_resource":
        url = extract_url(text)
        if not url:
            await tg_send(chat_id, "Paste a URL (or type cancel).")
            return {"ok": True}
        append_resource_link(chat_id, user_id, title="Saved link", url=url, raw_text=text)
        set_mode(chat_id, "")
        await tg_send_buttons(chat_id, f"🔖 Saved to Learning Bag:\n{url}", main_menu_buttons())
        return {"ok": True}

    if mode == "awaiting_quiz_topic":
        t = text.strip().lower()
        if t == "quiz recent":
            topic = get_most_recent_topic(chat_id)
            if not topic:
                await tg_send(chat_id, 'No study items yet. First save one with: "I studied ..."')
                return {"ok": True}
        else:
            topic = text.strip()

        questions = build_quiz_questions(topic)
        kv_set(chat_id, "quiz_questions", json.dumps(questions))
        kv_set(chat_id, "quiz_answers", json.dumps([]))
        set_mode(chat_id, "awaiting_quiz_answer_0")

        await tg_send(chat_id, f"🧠 Quiz on: {topic}\n\n{questions[0]}\n\nReply with your answer (or type cancel).")
        return {"ok": True}

    if mode.startswith("awaiting_quiz_answer_"):
        try:
            idx = int(mode.split("_")[-1])
        except Exception:
            idx = 0

        q_raw = kv_get(chat_id, "quiz_questions")
        a_raw = kv_get(chat_id, "quiz_answers")
        if not q_raw:
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, "Quiz state was missing. Starting over.", main_menu_buttons())
            return {"ok": True}

        questions = json.loads(q_raw)
        answers = json.loads(a_raw) if a_raw else []

        answers.append(text)
        kv_set(chat_id, "quiz_answers", json.dumps(answers))

        next_idx = idx + 1
        if next_idx < len(questions):
            set_mode(chat_id, f"awaiting_quiz_answer_{next_idx}")
            await tg_send(chat_id, questions[next_idx] + "\n\nReply with your answer (or type cancel).")
            return {"ok": True}

        set_mode(chat_id, "")
        kv_del(chat_id, "quiz_questions")
        kv_del(chat_id, "quiz_answers")

        recap = []
        for i, (q, a) in enumerate(zip(questions, answers), start=1):
            recap.append(f"{i}) Q: {q}\n   A: {a}")

        await tg_send_buttons(
            chat_id,
            "✅ Quiz complete! Here’s your recap:\n\n" + "\n\n".join(recap),
            main_menu_buttons()
        )
        return {"ok": True}

    if is_recent(text):
        items = get_recent_study(chat_id, n=5)
        if not items:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            return {"ok": True}
        lines = [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
        await tg_send(chat_id, "📌 Recent study:\n" + "\n".join(lines))
        return {"ok": True}

    if is_recollect(text):
        item = get_random_study(chat_id)
        if not item:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            return {"ok": True}
        await tg_send(chat_id, f"🧠 Recollect:\n{item['topic']}\n({item['ts']})")
        return {"ok": True}

    topic = extract_study_topic(text)
    if topic:
        append_study(chat_id, user_id, username, topic, text)
        await tg_send(chat_id, f'✅ Saved. You studied: "{topic}"')
        return {"ok": True}

    await tg_send(chat_id, 'Got it. Type "help" for commands.')
    return {"ok": True}
