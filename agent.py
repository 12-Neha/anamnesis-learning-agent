import re

HELP_TEXT = (
    "Try:\n"
    "• I studied ...\n"
    "• recent\n"
    "• recollect\n"
    "• add resource (then paste a link)\n"
    "• cancel"
)

def norm(text: str) -> str:
    return (text or "").strip()

def is_help(text: str) -> bool:
    t = text.lower().strip()
    return t in {"help", "/help", "menu"}

def is_recent(text: str) -> bool:
    t = text.lower()
    return t in {"recent", "/recent"} or "what did i study recently" in t

def is_recollect(text: str) -> bool:
    t = text.lower().strip()
    return t in {"recollect", "/recollect", "random"} or "random note" in t

def is_add_resource(text: str) -> bool:
    t = text.lower().strip()
    return t == "add resource" or t.startswith("add resource")

def is_cancel(text: str) -> bool:
    return text.lower().strip() in {"cancel", "/cancel"}

def extract_study_topic(text: str):
    # "I studied EOQ", "I learned about EOQ"
    m = re.match(r"^i\s+(studied|learned)\s+(.+)$", text, flags=re.I)
    return m.group(2).strip() if m else None

def extract_url(text: str):
    m = re.search(r"(https?://\S+)", text, flags=re.I)
    return m.group(1) if m else None
