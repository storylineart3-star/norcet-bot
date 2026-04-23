import os
import json
import gzip
import random
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load question bank
with gzip.open("nursing_questions.json.gz", "rt", encoding="utf=8") as f:

   QUESTIONS = json.load(f)
logger.info(f"Loaded {len(QUESTIONS)} nursing questions.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🩺 NORCET Quiz Bot ready!\n"
        "Use /quiz to get a random nursing question."
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await update.message.reply_text(
        f"❓ {q['question']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, chosen_str, correct_str = query.data.split(":")
    chosen = int(chosen_str)
    correct = int(correct_str)

    q = context.chat_data.get("last_question")
    if not q:
        await query.edit_message_text("⚠️ Question expired. Use /quiz again.")
        return

    options = q["options"]
    explanation = q.get("explanation", "No explanation available.")

    if chosen == correct:
        result = "✅ Correct!"
    else:
        result = f"❌ Wrong! The correct answer is {chr(65+correct)}. {options[correct]}"

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
    )

def main():
    TOKEN = os.environ["BOT_TOKEN"]
    PORT = int(os.environ.get("PORT", 8000))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(button_handler))

    webhook_path = "/telegram"
    webhook_url = f"{RENDER_URL}{webhook_path}"

    logger.info(f"Starting webhook on port {PORT}, URL: {webhook_url}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        secret_token="NorcetSecret123",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()