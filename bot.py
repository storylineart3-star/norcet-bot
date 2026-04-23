import os
import json
import gzip
import random
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Configuration ==========
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
DATA_DIR = "data"
PORT = int(os.environ.get("PORT", 8000))

# ========== Health-check server (runs in a thread) ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_health_server(port):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health server listening on port {port}")
    server.serve_forever()

# ========== Persistent storage ==========
def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_json(filename, default):
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(filename, data):
    ensure_data_dir()
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== Load question bank ==========
with gzip.open("nursing_questions.json.gz", "rt", encoding="utf-8") as f:
    QUESTIONS = json.load(f)
logger.info(f"Loaded {len(QUESTIONS)} nursing questions.")

SUBJECTS = sorted({q["subject"] for q in QUESTIONS})

# ========== Persistent user data ==========
users = set(load_json("users.json", []))
user_scores = load_json("scores.json", {})
bot_stats = load_json("bot_stats.json", {"total_answers": 0})

def save_users():
    save_json("users.json", list(users))

def save_scores():
    save_json("scores.json", user_scores)

def save_bot_stats():
    save_json("bot_stats.json", bot_stats)

def register_user(user_id: int):
    if user_id not in users:
        users.add(user_id)
        save_users()

# ========== Quiz session helpers ==========
def init_quiz_session(questions: list):
    """Create a new session dict."""
    return {
        "questions": questions,
        "index": 0,
        "correct": 0,
        "incorrect": 0,
        "skipped": 0,
    }

async def send_question_message(chat_id, context: ContextTypes.DEFAULT_TYPE, session, q):
    """Send a single question (current index) with inline buttons including Skip."""
    idx = session["index"]
    total = len(session["questions"])
    options = q["options"]
    correct_idx = q["correct_index"]

    # Build keyboard: answer buttons + skip button
    keyboard = [
        [InlineKeyboardButton(f"A. {options[0]}", callback_data=f"ans:0:{correct_idx}")],
        [InlineKeyboardButton(f"B. {options[1]}", callback_data=f"ans:1:{correct_idx}")],
        [InlineKeyboardButton(f"C. {options[2]}", callback_data=f"ans:2:{correct_idx}")],
        [InlineKeyboardButton(f"D. {options[3]}", callback_data=f"ans:3:{correct_idx}")],
        [InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{correct_idx}")],
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📌 *Question {idx+1}/{total}*\n\n❓ {q['question']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

# ========== Handlers ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await update.message.reply_text(
        "🩺 NORCET Quiz Bot ready!\nUse /help to see all commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📚 *NORCET Quiz Bot Help*\n\n"
        "/start – Welcome message\n"
        "/quiz – Random 1 question\n"
        "/quiz 10 – Start a 10‑question quiz (one by one with skip)\n"
        "/subjects – List available subjects\n"
        "/subject Anatomy – 1 question from Anatomy\n"
        "/subject Anatomy 5 – 5 questions from Anatomy (one by one)\n"
        "/stats – Your personal score (persistent)\n"
        "/help – Show this message\n\n"
        "👑 *Owner only:*\n"
        "/broadcast <text> – Send message to all users\n"
        "/botstats – Total users & answers"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)

    # Parse number of questions
    args = context.args
    if args and args[0].isdigit():
        count = int(args[0])
        count = max(1, min(count, 50))
    else:
        count = 1

    # Pick random questions and create session
    chosen = random.sample(QUESTIONS, min(count, len(QUESTIONS)))
    session = init_quiz_session(chosen)
    context.chat_data["quiz_session"] = session

    # Send first question
    await send_question_message(update.effective_chat.id, context, session, chosen[0])

async def subjects_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📖 *Available subjects:*\n" + "\n".join([f"• {s}" for s in SUBJECTS])
    await update.message.reply_text(text, parse_mode="Markdown")

async def subject_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)

    args = context.args
    if not args:
        await update.message.reply_text("Please specify a subject. Example: /subject Anatomy")
        return

    # Last argument may be a number
    count = 1
    if args[-1].isdigit():
        count = int(args[-1])
        count = max(1, min(count, 50))
        subject_name = " ".join(args[:-1]).strip()
    else:
        subject_name = " ".join(args).strip()

    matching = [q for q in QUESTIONS if q["subject"].lower() == subject_name.lower()]
    if not matching:
        await update.message.reply_text(
            f"Subject '{subject_name}' not found. Use /subjects to see the list."
        )
        return

    chosen = random.sample(matching, min(count, len(matching)))
    session = init_quiz_session(chosen)
    context.chat_data["quiz_session"] = session

    await send_question_message(update.effective_chat.id, context, session, chosen[0])

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)

    data = user_scores.get(str(user_id), {"correct": 0, "total": 0})
    correct = data["correct"]
    total = data["total"]
    percent = (correct / total * 100) if total > 0 else 0

    await update.message.reply_text(
        f"📊 *Your Stats*\n✅ Correct: {correct}\n❌ Wrong: {total - correct}\n📝 Total: {total}\n🎯 Accuracy: {percent:.1f}%",
        parse_mode="Markdown",
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "done":
        return  # ignore disabled buttons

    user_id = query.from_user.id
    register_user(user_id)

    session = context.chat_data.get("quiz_session")
    if not session:
        await query.edit_message_text("⚠️ No active quiz. Start a new one with /quiz.")
        return

    # Parse callback data
    if data.startswith("ans:"):
        # Answer pressed
        _, chosen_str, correct_str = data.split(":")
        chosen = int(chosen_str)
        correct = int(correct_str)
        is_skip = False
    elif data.startswith("skip:"):
        # Skip pressed
        _, correct_str = data.split(":")
        correct = int(correct_str)
        chosen = None
        is_skip = True
    else:
        return  # unknown

    # Get current question from session
    idx = session["index"]
    q = session["questions"][idx]
    options = q["options"]
    explanation = q.get("explanation", "No explanation available.")

    # Update session counters and global stats
    uid = str(user_id)
    if uid not in user_scores:
        user_scores[uid] = {"correct": 0, "total": 0}

    if is_skip:
        session["skipped"] += 1
        result_line = "⏭️ Skipped"
    else:
        user_scores[uid]["total"] += 1
        bot_stats["total_answers"] = bot_stats.get("total_answers", 0) + 1
        save_bot_stats()

        if chosen == correct:
            session["correct"] += 1
            user_scores[uid]["correct"] += 1
            result_line = "✅ Correct!"
        else:
            session["incorrect"] += 1
            result_line = f"❌ Wrong! The correct answer is {chr(65+correct)}. {options[correct]}"
        save_scores()

    # Build result message
    message = f"{result_line}\n\n📘 *Explanation:* {explanation}"

    # Disable buttons (show only the options without callback)
    disabled_keyboard = [
        [InlineKeyboardButton(f"A. {options[0]}", callback_data="done")],
        [InlineKeyboardButton(f"B. {options[1]}", callback_data="done")],
        [InlineKeyboardButton(f"C. {options[2]}", callback_data="done")],
        [InlineKeyboardButton(f"D. {options[3]}", callback_data="done")],
        [InlineKeyboardButton("⏭️ Skip", callback_data="done")],
    ]

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(disabled_keyboard),
        parse_mode="Markdown",
    )

    # Move to next question
    session["index"] += 1

    if session["index"] < len(session["questions"]):
        # Send next question
        next_q = session["questions"][session["index"]]
        await send_question_message(query.message.chat_id, context, session, next_q)
    else:
        # Quiz finished – show summary
        total = len(session["questions"])
        correct = session["correct"]
        incorrect = session["incorrect"]
        skipped = session["skipped"]
        percent = (correct / total * 100) if total > 0 else 0

        summary = (
            "🏁 *Quiz Completed!*\n\n"
            f"📝 Total questions: {total}\n"
            f"✅ Correct: {correct}\n"
            f"❌ Incorrect: {incorrect}\n"
            f"⏭️ Skipped: {skipped}\n"
            f"🎯 Accuracy: {percent:.1f}%"
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=summary,
            parse_mode="Markdown",
        )
        # Clear session
        del context.chat_data["quiz_session"]

# ========== Owner Commands ==========
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ You are not authorised.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    message_text = " ".join(context.args)
    if not users:
        await update.message.reply_text("No users yet.")
        return

    sent = 0
    failed = 0
    await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")

    for uid in list(users):
        try:
            await context.bot.send_message(chat_id=uid, text=message_text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}"
    )

async def botstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ You are not authorised.")
        return

    total_users = len(users)
    total_answers = bot_stats.get("total_answers", 0)
    await update.message.reply_text(
        f"📈 *Bot Statistics*\n👥 Total users: {total_users}\n💬 Total answers: {total_answers}",
        parse_mode="Markdown",
    )

# ========== Main ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("subjects", subjects_list))
    app.add_handler(CommandHandler("subject", subject_quiz))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("botstats", botstats))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Start health-check server in a daemon thread
    health_thread = threading.Thread(target=run_health_server, args=(PORT,), daemon=True)
    health_thread.start()

    # Start polling (blocking call)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
