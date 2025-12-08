# api/index.py
import os
import sys
import json
import logging
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Finman")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not all([BOT_TOKEN, MONGO_URI, WEBHOOK_URL]):
    logger.error("❌ Missing: TELEGRAM_BOT_TOKEN, MONGO_URI, WEBHOOK_URL")
    raise ValueError("Missing required env vars")

logger.info(f"✅ Config loaded")
logger.info(f"✅ Webhook URL: {WEBHOOK_URL}")

app = FastAPI()

bot_app = None

async def init_bot():
    """Initialize bot with all handlers from main.py"""
    global bot_app
    
    if bot_app is not None:
        return
    
    logger.info("🤖 Initializing bot...")
    
    try:
        # Create Application
        bot_app = Application.builder().token(BOT_TOKEN).build()
        
        # Connect MongoDB
        client = AsyncIOMotorClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        bot_app.bot_data["db"] = db
        
        logger.info("✅ MongoDB connected")
        
        # Create indexes
        try:
            await db.users.create_index("phone_number", unique=True)
            await db.queries.create_index("phone_number")
            logger.info("✅ Indexes created")
        except:
            pass
        
        # Import and register handlers from main.py
        from main import (
            start_command,
            contact_handler,
            message_handler,
            build_auth_handler,
        )
        
        # Register auth handler
        if build_auth_handler:
            try:
                bot_app.add_handler(build_auth_handler())
                logger.info("✅ Auth handler registered")
            except Exception as e:
                logger.warning(f"⚠️ Auth handler: {e}")
        
        # Register core handlers (same order as main.py)
        bot_app.add_handler(CommandHandler("start", start_command))
        bot_app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
        bot_app.add_handler(MessageHandler(filters.ALL & ~filters.CONTACT, message_handler))
        
        logger.info("✅ All handlers registered")
        logger.info("✅ Bot ready")
        
    except Exception as e:
        logger.error(f"❌ Bot init failed: {e}", exc_info=True)
        raise

@app.on_event("startup")
async def startup():
    try:
        await init_bot()
    except Exception as e:
        logger.error(f"Startup error: {e}")

@app.post("/webhook")
async def webhook(request: Request):
    """Telegram webhook endpoint"""
    if bot_app is None:
        await init_bot()
    
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot.bot)
        
        if update:
            await bot_app.process_update(update)
            logger.info(f"✅ Update {update.update_id} processed")
            return {"ok": True}
        return {"ok": False}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

@app.post("/set-webhook")
async def set_webhook():
    """Register webhook with Telegram"""
    if bot_app is None:
        await init_bot()
    
    try:
        result = await bot_app.bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=["message", "contact"]
        )
        logger.info(f"✅ Webhook set: {WEBHOOK_URL}")
        return {"ok": True, "url": WEBHOOK_URL, "result": result}
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/webhook-info")
async def webhook_info():
    """Get webhook status from Telegram"""
    if bot_app is None:
        await init_bot()
    
    try:
        info = await bot_app.bot.get_webhook_info()
        return {
            "url": info.url,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "healthy", "bot": "active"}

@app.get("/")
async def root():
    return {
        "name": "Budget Manager Bot",
        "mode": "webhook",
        "status": "running"
    }