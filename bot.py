import os
import json
import gzip
import random
import logging
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import aiohttp.web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Configuration ==========
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
DATA_DIR = "data"
PORT = int(os.environ.get("PORT", 8000))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

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
def pick_questions(count, subject=None):
    """Return a list of count random questions, optionally filtered by subject."""
    if subject:
        pool = [q for q in QUESTIONS if q["subject"].lower() == subject.lower()]
        if not pool:
            return []
    else:
        pool = QUESTIONS
    return random.sample(pool, min(count, len(pool)))

def init_quiz_session(questions: list):
    return {
        "questions": questions,
        "index": 0,
        "correct": 0,
        "incorrect": 0,
        "skipped": 0,
    }

async def send_question_message(chat_id, context: ContextTypes.DEFAULT_TYPE, session, q):
    idx = session["index"]
    total = len(session["questions"])
    options = q["options"]
    correct_idx = q["correct_index"]

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

async def finish_quiz(chat_id, context, session):
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
        chat_id=chat_id,
        text=summary,
        parse_mode="Markdown",
    )

# ========== Quick-start buttons (callback handlers) ==========
QUICK_ACTIONS = {
    "quick_20": (20, None, "20 random questions"),
    "quick_anatomy20": (20, "Anatomy", "20 Anatomy questions"),
    "quick_50": (50, None, "50 random questions"),
    "quick_rand50": (50, None, "50 random questions"),
}

# ========== Handlers ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)

    keyboard = [
        [InlineKeyboardButton("🎲 Quick 20 Q&A", callback_data="quick_20")],
        [InlineKeyboardButton("🦴 Anatomy 20", callback_data="quick_anatomy20")],
        [InlineKeyboardButton("📚 Quick 50 Q&A", callback_data="quick_50")],
        [InlineKeyboardButton("🔀 Random 50 Q&A", callback_data="quick_rand50")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help_button")],
    ]

    await update.message.reply_text(
        "👋 *Welcome to NORCET Quiz Bot!*\n\n"
        "Choose a quick test or type /help for all commands.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📚 *NORCET Quiz Bot – Full Help*\n\n"
        "*Core Commands:*\n"
        "/start – Welcome message with quick tests\n"
        "/help – This detailed guide\n"
        "/quiz – 1 random question\n"
        "/quiz 10 – Start a 10‑question quiz (one by one, with skip)\n"
        "/subjects – List all subjects\n"
        "/subject Pharmacology – 1 question from Pharmacology\n"
        "/subject Anatomy 20 – 20 Anatomy questions (one by one)\n\n"
        "*During a quiz:*\n"
        "Each question shows A/B/C/D buttons + a ⏭️ Skip button.\n"
        "After you answer or skip, you see the explanation and the next question appears.\n"
        "A progress indicator like *📌 Question 3/10* keeps you on track.\n"
        "At the end, you get a summary: correct, incorrect, skipped, accuracy.\n\n"
        "*Your stats:*\n"
        "/stats – View your total correct/wrong/accuracy (persistent across sessions)\n\n"
        "*Owner only:*\n"
        "/broadcast <message> – Send a message to all users\n"
        "/botstats – See total users and answers\n\n"
        "💡 *Tip:* You can always stop a quiz by starting a new one with /quiz."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)

    args = context.args
    count = int(args[0]) if (args and args[0].isdigit()) else 1
    count = max(1, min(count, 50))

    questions = pick_questions(count)
    session = init_quiz_session(questions)
    context.chat_data["quiz_session"] = session

    await send_question_message(update.effective_chat.id, context, session, questions[0])

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

    questions = pick_questions(count, subject=subject_name)
    if not questions:
        await update.message.reply_text(
            f"Subject '{subject_name}' not found. Use /subjects to see the list."
        )
        return

    session = init_quiz_session(questions)
    context.chat_data["quiz_session"] = session

    await send_question_message(update.effective_chat.id, context, session, questions[0])

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
        return

    # ----- handle quick-start buttons -----
    if data in QUICK_ACTIONS:
        count, subject, _ = QUICK_ACTIONS[data]
        user_id = query.from_user.id
        register_user(user_id)
        questions = pick_questions(count, subject=subject)
        if not questions:
            await query.edit_message_text("No questions found for that subject.")
            return
        session = init_quiz_session(questions)
        context.chat_data["quiz_session"] = session
        await send_question_message(query.message.chat_id, context, session, questions[0])
        return

    # ----- handle help button -----
    if data == "help_button":
        await help_command(update, context)
        return

    # ----- normal quiz answer/skip -----
    session = context.chat_data.get("quiz_session")
    if not session:
        await query.edit_message_text("⚠️ No active quiz. Start a new one with /quiz.")
        return

    # Parse callback data
    if data.startswith("ans:"):
        _, chosen_str, correct_str = data.split(":")
        chosen = int(chosen_str)
        correct = int(correct_str)
        is_skip = False
    elif data.startswith("skip:"):
        _, correct_str = data.split(":")
        correct = int(correct_str)
        chosen = None
        is_skip = True
    else:
        return

    idx = session["index"]
    q = session["questions"][idx]
    options = q["options"]
    explanation = q.get("explanation", "No explanation available.")

    uid = str(query.from_user.id)
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

    message = f"{result_line}\n\n📘 *Explanation:* {explanation}"

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

    # Advance to next question
    session["index"] += 1

    if session["index"] < len(session["questions"]):
        next_q = session["questions"][session["index"]]
        await send_question_message(query.message.chat_id, context, session, next_q)
    else:
        await finish_quiz(query.message.chat_id, context, session)
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

# ========== Custom aiohttp app and Main Entry ==========
async def main():
    if not RENDER_URL:
        raise RuntimeError("Missing RENDER_EXTERNAL_URL environment variable")

    # Build the Application
    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("subjects", subjects_list))
    app.add_handler(CommandHandler("subject", subject_quiz))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("botstats", botstats))
    app.add_handler(CallbackQueryHandler(button_handler))

    # 1. Set the webhook with Telegram API
    webhook_url = f"{RENDER_URL}/telegram"
    await app.bot.set_webhook(
        url=webhook_url,
        secret_token="NorcetSecret123",
        drop_pending_updates=True
    )

    # 2. Setup custom aiohttp server to receive updates
    async def telegram_webhook(request):
        # Verify secret token
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != "NorcetSecret123":
            return aiohttp.web.Response(status=403)
            
        update_data = await request.json()
        await app.update_queue.put(Update.de_json(data=update_data, bot=app.bot))
        return aiohttp.web.Response()

    async def health(request):
        return aiohttp.web.Response(text="OK")

    web_app = aiohttp.web.Application()
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_get("/", health)

    # 3. Start aiohttp server and bot app concurrently
    runner = aiohttp.web.AppRunner(web_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", PORT)
    
    logger.info(f"Starting custom aiohttp webhook on port {PORT}, URL: {webhook_url}")
    
    async with app:
        await app.start()
        await site.start()
        
        # Keep the process running
        stop_signal = asyncio.Event()
        await stop_signal.wait()

if __name__ == "__main__":
    asyncio.run(main())
    
