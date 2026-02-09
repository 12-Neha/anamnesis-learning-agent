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

def check_dashboard_auth(req: Request):
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
        [{"text": "🔁 Nudge me", "callback_data": "menu_nudge"},
         {"text": "❌ Cancel", "callback_data": "menu_cancel"}],
    ]

@app.get("/")
async def health():
    return {"ok": True}

class StudyIn(BaseModel):
    topic: str
    raw_text: str | None = None
    username: str | None = None
    user_id: str | None = None
    chat_id: str | None = "dashboard"

@app.post("/api/study")
async def api_save_study(payload: StudyIn, req: Request):
    check_dashboard_auth(req)
    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    chat_id, user_id = str(payload.chat_id or "dashboard"), str(payload.user_id or "dashboard")
    username, raw = payload.username or "dashboard", payload.raw_text or topic
    append_study(chat_id, user_id, username, topic, raw)
    return {"ok": True, "topic": topic}

@app.get("/api/study/recent")
async def api_recent(n: int = 20, chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    items = get_recent_study(str(chat_id), n=n)
    return {"ok": True, "items": items}

@app.get("/api/recollect")
async def api_recollect(chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    item = get_random_study(str(chat_id))
    return {"ok": True, "item": item}

@app.get("/api/nudge/next")
async def api_nudge(chat_id: str = "dashboard", req: Request = None):
    check_dashboard_auth(req)
    item = get_due_item(str(chat_id)) or get_next_item_anytime(str(chat_id))
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
    append_resource_link(str(payload.chat_id or "dashboard"), str(payload.user_id or "dashboard"),
                         title=payload.title or "Saved link", url=url, raw_text=payload.raw_text or url)
    return {"ok": True, "url": url}

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}
    cb = update.get("callback_query")
    if cb:
        chat_id = str((cb.get("message") or {}).get("chat", {}).get("id", ""))
        user = cb.get("from") or {}
        user_id, data = str(user.get("id", "")), cb.get("data", "")
        if not chat_id or not allowed(user_id):
            return {"ok": True}
        if data == "menu_recent":
            items = get_recent_study(chat_id, n=5)
            out = "No study items yet." if not items else "📌 Recent:\n" + "\n".join([f"{i+1}) {it['topic']}" for i, it in enumerate(items)])
            await tg_send(chat_id, out)
        elif data == "menu_recollect":
            item = get_random_study(chat_id)
            out = 'No items yet.' if not item else f"🧠 Recollect:\n{item['topic']}"
            await tg_send(chat_id, out)
        elif data == "menu_add_resource":
            set_mode(chat_id, "awaiting_resource")
            await tg_send(chat_id, "🎒 Paste a link to save.")
        elif data == "menu_record":
            set_mode(chat_id, "awaiting_study")
            await tg_send(chat_id, '📝 Send topic.')
        elif data == "menu_nudge":
            item = get_due_item(chat_id) or get_next_item_anytime(chat_id)
            if not item: await tg_send(chat_id, "No recall items yet.")
            else:
                try: await send_recall_prompt(chat_id, item)
                except Exception: await tg_send(chat_id, f"🔁 Nudge:\n{item.get('topic')}")
        elif data == "menu_cancel":
            set_mode(chat_id, "")
            await tg_send(chat_id, "✅ Cancelled.")
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb.get("id")})
        return {"ok": True}

    msg = update.get("message") or update.get("edited_message")
    if not msg: return {"ok": True}
    chat_id, user = str((msg.get("chat") or {}).get("id", "")), msg.get("from") or {}
    user_id, username, text = str(user.get("id", "")), user.get("username", "") or "", norm(msg.get("text", ""))
    if not chat_id or not allowed(user_id): return {"ok": True}
    if is_help(text):
        await tg_send_buttons(chat_id, "Choose an action:", main_menu_buttons())
        return {"ok": True}
    if is_cancel(text):
        set_mode(chat_id, ""); await tg_send(chat_id, "✅ Cancelled."); return {"ok": True}
    mode = get_mode(chat_id)
    if mode == "awaiting_study" and text:
        topic = extract_study_topic(text) or text
        append_study(chat_id, user_id, username, topic, text)
        set_mode(chat_id, ""); await tg_send_buttons(chat_id, f'✅ Saved: "{topic}"', main_menu_buttons())
        return {"ok": True}
    if mode == "awaiting_resource":
        url = extract_url(text)
        if not url: await tg_send(chat_id, "Paste a URL."); return {"ok": True}
        append_resource_link(chat_id, user_id, title="Saved link", url=url, raw_text=text)
        set_mode(chat_id, ""); await tg_send(chat_id, f"🔖 Saved:\n{url}"); return {"ok": True}
    if is_recent(text):
        items = get_recent_study(chat_id, n=5)
        if not items: await tg_send(chat_id, 'No items.'); return {"ok": True}
        await tg_send(chat_id, "📌 Recent:\n" + "\n".join([f"{i+1}) {it['topic']}" for i, it in enumerate(items)]))
        return {"ok": True}
    topic = extract_study_topic(text)
    if topic:
        append_study(chat_id, user_id, username, topic, text)
        await tg_send(chat_id, f'✅ Saved: "{topic}"'); return {"ok": True}
    await tg_send(chat_id, 'Got it. Type "help" for commands.')
    return {"ok": True}
