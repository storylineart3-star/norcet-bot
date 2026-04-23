import os
import json
import gzip
import random
import logging
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
        pass  # keep logs clean

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

# ========== Helpers ==========
async def send_question(chat_id, context: ContextTypes.DEFAULT_TYPE, q=None):
    if q is None:
        q = random.choice(QUESTIONS)
    options = q["options"]
    correct_idx = q["correct_index"]

    keyboard = [
        [InlineKeyboardButton(f"A. {options[0]}", callback_data=f"ans:0:{correct_idx}")],
        [InlineKeyboardButton(f"B. {options[1]}", callback_data=f"ans:1:{correct_idx}")],
        [InlineKeyboardButton(f"C. {options[2]}", callback_data=f"ans:2:{correct_idx}")],
        [InlineKeyboardButton(f"D. {options[3]}", callback_data=f"ans:3:{correct_idx}")],
    ]
    context.chat_data["last_question"] = q

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"❓ {q['question']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
        "/quiz 10 – Random 10 questions (max 50)\n"
        "/subjects – List available subjects\n"
        "/subject Anatomy – 1 question from Anatomy\n"
        "/subject Anatomy 5 – 5 questions from Anatomy\n"
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

    args = context.args
    if args and args[0].isdigit():
        count = int(args[0])
        count = max(1, min(count, 50))
    else:
        count = 1

    chosen = random.sample(QUESTIONS, min(count, len(QUESTIONS)))
    for q in chosen:
        await send_question(update.effective_chat.id, context, q)

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
    for q in chosen:
        await send_question(update.effective_chat.id, context, q)

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

    if query.data == "done":
        return

    user_id = query.from_user.id
    register_user(user_id)

    _, chosen_str, correct_str = query.data.split(":")
    chosen = int(chosen_str)
    correct = int(correct_str)

    q = context.chat_data.get("last_question")
    if not q:
        await query.edit_message_text("⚠️ Question expired. Use /quiz again.")
        return

    options = q["options"]
    explanation = q.get("explanation", "No explanation available.")

    uid = str(user_id)
    if uid not in user_scores:
        user_scores[uid] = {"correct": 0, "total": 0}
    user_scores[uid]["total"] += 1
    if chosen == correct:
        user_scores[uid]["correct"] += 1
        result = "✅ Correct!"
    else:
        result = f"❌ Wrong! The correct answer is {chr(65+correct)}. {options[correct]}"
    save_scores()

    bot_stats["total_answers"] = bot_stats.get("total_answers", 0) + 1
    save_bot_stats()

    message = f"{result}\n\n📘 *Explanation:* {explanation}"

    disabled_keyboard = [
        [InlineKeyboardButton(f"A. {options[0]}", callback_data="done")],
        [InlineKeyboardButton(f"B. {options[1]}", callback_data="done")],
        [InlineKeyboardButton(f"C. {options[2]}", callback_data="done")],
        [InlineKeyboardButton(f"D. {options[3]}", callback_data="done")],
    ]
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(disabled_keyboard),
        parse_mode="Markdown",
    )

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

    # Register all handlers
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

    # Start polling (blocking call – keeps the bot alive)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
