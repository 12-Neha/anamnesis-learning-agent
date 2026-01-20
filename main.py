import os
import httpx
from fastapi import FastAPI, Request
from dotenv import load_dotenv

from db import (
    init_db, append_study, get_recent_study, get_random_study,
    set_mode, get_mode, append_resource_link
)
from agent import (
    norm, is_help, is_recent, is_recollect, is_add_resource, is_cancel,
    extract_study_topic, extract_url, HELP_TEXT
)

load_dotenv()
app = FastAPI()
init_db()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()  # optional

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

async def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def allowed(user_id: str) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)

@app.get("/")
async def health():
    return {"ok": True}

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    # Always return 200 quickly to avoid Telegram retries
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = str((msg.get("chat") or {}).get("id", ""))
    user = msg.get("from") or {}
    user_id = str(user.get("id", ""))
    username = user.get("username", "") or ""
    text = norm(msg.get("text", ""))

    if not chat_id:
        return {"ok": True}

    if not allowed(user_id):
        # silently ignore others for safety
        return {"ok": True}

    # 1) help
    if is_help(text):
        await tg_send(chat_id, HELP_TEXT)
        return {"ok": True}

    # 2) cancel mode
    if is_cancel(text):
        set_mode(chat_id, "")
        await tg_send(chat_id, "✅ Cancelled.")
        return {"ok": True}

    # 3) recent
    if is_recent(text):
        items = get_recent_study(chat_id, n=5)
        if not items:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            return {"ok": True}
        lines = [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
        await tg_send(chat_id, "📌 Recent study:\n" + "\n".join(lines))
        return {"ok": True}

    # 4) recollect random
    if is_recollect(text):
        item = get_random_study(chat_id)
        if not item:
            await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            return {"ok": True}
        await tg_send(chat_id, f"🧠 Recollect:\n{item['topic']}\n({item['ts']})")
        return {"ok": True}

    # 5) add resource mode
    if is_add_resource(text):
        set_mode(chat_id, "awaiting_resource")
        await tg_send(chat_id, "🎒 Send a link to save (or type cancel).")
        return {"ok": True}

    # 6) if awaiting resource, save link
    mode = get_mode(chat_id)
    if mode == "awaiting_resource":
        url = extract_url(text)
        if not url:
            await tg_send(chat_id, "Paste a URL (or type cancel).")
            return {"ok": True}
        append_resource_link(chat_id, user_id, title="Saved link", url=url, raw_text=text)
        set_mode(chat_id, "")
        await tg_send(chat_id, f"🔖 Saved to Learning Bag:\n{url}")
        return {"ok": True}

    # 7) study ingestion
    topic = extract_study_topic(text)
    if topic:
        append_study(chat_id, user_id, username, topic, text)
        await tg_send(chat_id, f'✅ Saved. You studied: "{topic}"')
        return {"ok": True}

    # fallback
    await tg_send(chat_id, 'Got it. Type "help" for commands.')
    return {"ok": True}
