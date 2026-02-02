# Anamnesis AI – Agentic Learning & Memory Companion

Anamnesis AI is a **Telegram-based agentic learning companion** designed to solve a personal knowledge-retention problem:  
*I consume a lot of books, courses, and articles — but after a few days, I forget most of it.*

This project converts passive learning into **retrievable, reinforced memory** using agentic workflows such as recall, quizzes, and spaced repetition.

---

## 🚀 What It Does

- 📝 **Capture learning** via natural language (e.g., “I studied EOQ”)
- 📌 **Deterministic memory** of what was studied, when, and in what context (no hallucinations)
- 🧠 **Active recall** through random recollection and spaced repetition nudges
- ❓ **Quiz Agent (LLM-powered)** that:
  - Generates questions on a topic
  - Grades answers with structured feedback
  - Highlights strengths, gaps, and ideal answers
- 🎒 **Learning Bag** to store links and resources
- 🔁 **Agentic workflows** using conversational state, buttons, and callbacks (not just prompts)

---

## 🧠 Why Agentic AI (Not Just a Chatbot)

Unlike typical chatbots, Anamnesis AI:
- Maintains **explicit state** across interactions
- Separates **memory (SQLite)** from **reasoning (LLM)**
- Uses **deterministic storage** for recall and LLMs only where judgment is needed (quiz grading)
- Orchestrates multi-step workflows (capture → recall → evaluate → reinforce)

This makes the system **reliable, extensible, and production-safe**.

---

## 🏗 Architecture

- **Interface:** Telegram Bot (buttons + chat)
- **Backend:** FastAPI (Python)
- **Memory:** SQLite (study logs, resources, quiz sessions)
- **AI:** OpenAI (Responses API) for quiz generation & grading
- **Deployment:** Render (webhooks, production-ready)

Telegram → FastAPI Webhook → Agent Router
↓
Deterministic DB
↓
LLM (Evaluation only)


---

## ⚙️ Core Agent Workflows

### A) Learning Capture
- Input: “I studied X”
- Action: Store topic + timestamp, enqueue for recall

### B) Recall & Spaced Repetition
- “Nudge me” triggers recall prompts
- User feedback adapts future intervals

### C) Quiz Agent (LLM-powered)
- Topic → questions → graded answers → feedback
- Stored for future reinforcement

### D) Resource Memory
- Save articles, links, and references for later summarization

---

## 🔐 Safety & Reliability

- No hallucinated memory — all recall is deterministic
- LLM usage is constrained to evaluation tasks
- Secrets managed via environment variables (no keys in code)

---

## 🛠 Tech Stack

- Python, FastAPI
- Telegram Bot API
- SQLite
- OpenAI Responses API
- Render

---

## 📈 Future Extensions

- Auto-summarization & flashcard generation
- Audio recall prompts
- Weekly topic-based news agent
- Calendar-based recall reminders

---

## 📌 Status

Actively developed as a personal learning system and portfolio project.
