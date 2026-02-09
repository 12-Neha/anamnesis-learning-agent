import os, json
import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

API_URL = "https://api.openai.com/v1/responses"

def _headers():
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

async def llm_generate_quiz(topic: str, n: int = 5) -> list[dict]:
    """
    Returns: [{"q": "...", "ideal": "...(short outline)", "tags": ["..."]}, ...]
    """
    if not OPENAI_API_KEY:
        # Safe fallback: deterministic non-LLM questions
        return [{"q": f"Explain {topic} in 3 bullet points.", "ideal": "Definition + why it matters + example", "tags": ["fallback"]}]

    prompt = f"""
Create a short quiz on the topic: {topic}

Return ONLY valid JSON with this exact schema:
{{
  "items": [
    {{"q": "question text", "ideal": "ideal answer outline (2-4 bullets)", "tags": ["tag1","tag2"]}}
  ]
}}

Rules:
- Make {n} questions.
- Mix: definition, application, mini-case, common pitfall.
- Keep each question answerable in <90 seconds.
"""

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": "You generate quizzes and must output strict JSON only."},
            {"role": "user", "content": prompt.strip()},
        ],
    }

    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    # Responses API returns content in output items; we’ll extract the first text block.
    text = ""
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text"):
                text += c.get("text", "")

    text = text.strip()
    obj = json.loads(text)
    return obj["items"]

async def llm_grade_answer(topic: str, question: str, ideal: str, user_answer: str) -> dict:
    """
    Returns:
    {
      "score": 0-10,
      "verdict": "one-liner",
      "what_was_good": ["..."],
      "what_to_improve": ["..."],
      "model_answer": "short improved answer"
    }
    """
    if not OPENAI_API_KEY:
        return {
            "score": 6,
            "verdict": "Fallback grading (no API key set).",
            "what_was_good": ["You attempted an answer."],
            "what_to_improve": ["Add more structure and an example."],
            "model_answer": ideal,
        }

    prompt = f"""
Topic: {topic}

Question: {question}

Ideal answer outline:
{ideal}

User answer:
{user_answer}

Grade the user answer strictly vs the ideal outline.
Return ONLY valid JSON with exactly:
{{
  "score": 0-10,
  "verdict": "one sentence",
  "what_was_good": ["..."],
  "what_to_improve": ["..."],
  "model_answer": "a short corrected answer (max 6 lines)"
}}
"""

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": "You are a strict evaluator. Output strict JSON only."},
            {"role": "user", "content": prompt.strip()},
        ],
    }

    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    text = ""
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text"):
                text += c.get("text", "")

    return json.loads(text.strip())
