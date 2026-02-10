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
)

load_dotenv()
app = FastAPI()
init_db()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
VERCEL_ORIGIN = os.getenv("VERCEL_ORIGIN", "*").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[VERCEL_ORIGIN] if VERCEL_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def allowed(user_id: str) -> bool:
    return (not ALLOWED_USER_ID) or (str(user_id) == str(ALLOWED_USER_ID))

def check_dashboard_auth(req: Request):
    # Optional auth for dashboard; if DASHBOARD_TOKEN not set, auth is disabled
    if not DASHBOARD_TOKEN:
        return
    auth = req.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth.split(" ", 1)[1].strip()
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

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
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": buttons}}
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

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

# -----------------------
# Health
# -----------------------
@app.get("/")
async def health():
    return {"ok": True}

# -----------------------
# Dashboard API (works with Vercel)
# -----------------------
class StudyIn(BaseModel):
    topic: str
    raw_text: str | None = None
    username: str | None = "dashboard"
    user_id: str | None = "dashboard"
    chat_id: str | None = "dashboard"

@app.post("/api/study")
async def api_save_study(payload: StudyIn, req: Request):
    check_dashboard_auth(req)
    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    append_study(payload.chat_id, payload.user_id, payload.username, topic, payload.raw_text or topic)
    return {"ok": True, "topic": topic}

@app.get("/api/study/recent")
async def api_recent(n: int = 20, chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    items = get_recent_study(chat_id, n=n)
    return {"ok": True, "items": items}

@app.get("/api/recollect")
async def api_recollect(chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    item = get_random_study(chat_id)
    return {"ok": True, "item": item}

@app.get("/api/nudge/next")
async def api_nudge(chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    item = get_due_item(chat_id) or get_next_item_anytime(chat_id)
    return {"ok": True, "item": item}

class ResourceLinkIn(BaseModel):
    url: str
    title: str | None = "Saved link"
    raw_text: str | None = None
    user_id: str | None = "dashboard"
    chat_id: str | None = "dashboard"

@app.post("/api/resources/link")
async def api_save_link(payload: ResourceLinkIn, req: Request):
    check_dashboard_auth(req)
    url = (payload.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    append_resource_link(payload.chat_id, payload.user_id, payload.title or "Saved link", url, payload.raw_text or url)
    return {"ok": True, "url": url}

# -----------------------
# Telegram webhook
# -----------------------
@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}

    try:
        # 1) CALLBACKS (buttons)
        cb = update.get("callback_query")
        if cb:
            msg = cb.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            user = cb.get("from") or {}
            user_id = str(user.get("id", ""))
            data = cb.get("data", "")

            if not chat_id or not allowed(user_id):
                return {"ok": True}

            if data == "menu_recent":
                items = get_recent_study(chat_id, n=5)
                out = "No study items yet. Try: I studied EOQ" if not items else \
                      "📌 Recent study:\n" + "\n".join([f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)])
                await tg_send(chat_id, out)

            elif data == "menu_recollect":
                item = get_random_study(chat_id)
                out = 'No study items yet. Try: "I studied EOQ"' if not item else f"🧠 Recollect:\n{item['topic']}\n({item['ts']})"
                await tg_send(chat_id, out)

            elif data == "menu_nudge":
                item = get_due_item(chat_id) or get_next_item_anytime(chat_id)
                if not item:
                    await tg_send(chat_id, "No recall items yet. Save something with: I studied ...")
                else:
                    await tg_send(chat_id, f"🔁 Nudge:\n{item.get('topic')}\n({item.get('ts')})")

            elif data == "menu_add_resource":
                set_mode(chat_id, "awaiting_resource")
                await tg_send(chat_id, "🎒 Paste a link to save (or type cancel).")

            elif data == "menu_record":
                set_mode(chat_id, "awaiting_study")
                await tg_send(chat_id, '📝 Send: "I studied ..." or just type the topic.')

            elif data == "menu_quiz":
                # Placeholder until we implement full quiz agent
                set_mode(chat_id, "awaiting_quiz_topic")
                await tg_send(chat_id, "❓ What topic should I quiz you on?\nType a topic OR send: quiz recent")

            elif data == "menu_cancel":
                set_mode(chat_id, "")
                await tg_send(chat_id, "✅ Cancelled.")

            # Always acknowledge callback to stop Telegram loading spinner
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{TELEGRAM_API}/answerCallbackQuery",
                    json={"callback_query_id": cb.get("id")},
                )
            return {"ok": True}

        # 2) MESSAGES
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return {"ok": True}

        chat_id = str((msg.get("chat") or {}).get("id", ""))
        user = msg.get("from") or {}
        user_id = str(user.get("id", ""))
        username = user.get("username", "") or ""
        text_raw = msg.get("text", "") or ""
        text = text_raw.strip()

        if not chat_id or not allowed(user_id):
            return {"ok": True}

        # help/menu
        if is_help(norm(text)):
            await tg_send_buttons(chat_id, "Main Menu:", main_menu_buttons())
            return {"ok": True}

        # cancel
        if is_cancel(norm(text)):
            set_mode(chat_id, "")
            await tg_send(chat_id, "✅ Cancelled.")
            return {"ok": True}

        mode = get_mode(chat_id)

        # awaiting study
        if mode == "awaiting_study" and text:
            topic = extract_study_topic(text) or text
            append_study(chat_id, user_id, username, topic, text_raw)
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, f'✅ Saved: "{topic}"', main_menu_buttons())
            return {"ok": True}

        # awaiting resource
        if mode == "awaiting_resource":
            url = extract_url(text_raw)
            if not url:
                await tg_send(chat_id, "Paste a URL (or type cancel).")
                return {"ok": True}
            append_resource_link(chat_id, user_id, title="Saved link", url=url, raw_text=text_raw)
            set_mode(chat_id, "")
            await tg_send_buttons(chat_id, f"🔖 Saved to Learning Bag:\n{url}", main_menu_buttons())
            return {"ok": True}

        # awaiting quiz topic (placeholder)
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
            await tg_send(chat_id, f"🧠 Quiz coming next for: {topic}\n\nFor now: reply with 3 key takeaways + 1 example.")
            await tg_send_buttons(chat_id, "Main Menu:", main_menu_buttons())
            return {"ok": True}

        # recent/recollect commands
        if is_recent(norm(text)):
            items = get_recent_study(chat_id, n=5)
            if not items:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            else:
                lines = [f"{i+1}) {it['topic']} ({it['ts']})" for i, it in enumerate(items)]
                await tg_send(chat_id, "📌 Recent study:\n" + "\n".join(lines))
            return {"ok": True}

        if is_recollect(norm(text)):
            item = get_random_study(chat_id)
            if not item:
                await tg_send(chat_id, 'No study items yet. Try: "I studied EOQ"')
            else:
                await tg_send(chat_id, f"🧠 Recollect:\n{item['topic']}\n({item['ts']})")
            return {"ok": True}

        # natural ingestion "I studied ..."
        topic = extract_study_topic(text_raw)
        if topic:
            append_study(chat_id, user_id, username, topic, text_raw)
            await tg_send(chat_id, f'✅ Saved. You studied: "{topic}"')
            return {"ok": True}

        # fallback
        await tg_send(chat_id, 'Got it. Type "help" for menu.')
        return {"ok": True}

    except Exception as e:
        # Never let Telegram see a 500; log and return OK
        print("Webhook error:", repr(e))
        return {"ok": True}
