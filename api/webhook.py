# api/webhook.py
import os
import json
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Finman")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g., https://yourapp.vercel.app/webhook

if not all([BOT_TOKEN, MONGO_URI, WEBHOOK_URL]):
    raise ValueError("Missing required env vars: TELEGRAM_BOT_TOKEN, MONGO_URI, WEBHOOK_URL")

app = FastAPI()

# Initialize bot app
bot_app = Application.builder().token(BOT_TOKEN).build()

# Initialize MongoDB
client = AsyncIOMotorClient(MONGO_URI)
db = client[MONGO_DB_NAME]
bot_app.bot_data["db"] = db

# Import handlers from main.py
from main import (
    start_command, contact_handler, message_handler,
    _create_indexes, build_auth_handler
)
from telegram.ext import CommandHandler, MessageHandler, filters

# Register handlers
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.CONTACT, message_handler))

if build_auth_handler:
    try:
        bot_app.add_handler(build_auth_handler())
    except Exception as e:
        print(f"Failed to register auth handler: {e}")

@app.on_event("startup")
async def startup():
    """Initialize indexes on startup."""
    await _create_indexes(db)
    print("✅ Bot ready on Vercel webhook")

@app.post("/webhook")
async def webhook(request: Request):
    """Receive Telegram updates via webhook."""
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot.bot)
        await bot_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "bot": "active"}

@app.post("/set-webhook")
async def set_webhook(request: Request):
    """Manually trigger webhook registration (call once after deploy)."""
    try:
        await bot_app.bot.set_webhook(url=WEBHOOK_URL)
        return {"ok": True, "message": f"Webhook set to {WEBHOOK_URL}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}