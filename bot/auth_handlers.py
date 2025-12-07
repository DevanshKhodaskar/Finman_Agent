# bot/auth_handlers.py
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from datetime import datetime, timezone
from typing import Optional

from bot import user_model
from bot.sessions import create_session, get_session, set_session_state, destroy_session
from utils.phone_utils import normalize_phone
from utils.crypto import hash_password
from message_to_json import parse_message_to_entry, handle_image as message_handle_image

# States
CHOOSING, WAIT_CONTACT, ENTER_PASSWORD_CREATE, RESET_NEW_PASSWORD, ADD_QUERY = range(5)

# Keyboards
def main_menu_kb():
    return ReplyKeyboardMarkup(
        [["Create Account", "Authenticate"], ["Reset Password"]],
        one_time_keyboard=True, resize_keyboard=True
    )

def share_contact_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Share Contact 📱", request_contact=True)]],
        one_time_keyboard=True, resize_keyboard=True
    )

# Entry: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=main_menu_kb())
    context.user_data.pop("intent", None)
    return CHOOSING

# Handle main menu choice
async def choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    if text in ["create account", "authenticate", "reset password"]:
        context.user_data["intent"] = text.lower()
        await update.message.reply_text(
            "📱 Please share your contact using the button below.",
            reply_markup=share_contact_kb()
        )
        return WAIT_CONTACT
    await update.message.reply_text("Please select a valid option.", reply_markup=main_menu_kb())
    return CHOOSING

# Handle contact share
async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    contact = msg.contact
    tg_user = update.effective_user
    tg_id = tg_user.id

    if not contact or contact.user_id != tg_id:
        await msg.reply_text(
            "❌ Please share your own contact using the Share Contact button 📱.",
            reply_markup=share_contact_kb()
        )
        return WAIT_CONTACT

    phone = normalize_phone(contact.phone_number)
    if not phone:
        await msg.reply_text("Invalid phone number. Share a valid contact.")
        return WAIT_CONTACT

    create_session(tg_id, phone, authed=False)
    set_session_state(tg_id, "phone", phone)
    db = context.application.bot_data.get("db")
    if db is None:
        await msg.reply_text("Server unavailable.", reply_markup=ReplyKeyboardRemove())
        destroy_session(tg_id)
        return ConversationHandler.END

    user = await user_model.find_user_by_phone(db, phone)
    intent = context.user_data.get("intent", "authenticate")

    # RESET flow
    if intent == "reset password":
        if not user:
            await msg.reply_text("No account found. Use Create Account.", reply_markup=ReplyKeyboardRemove())
            destroy_session(tg_id)
            return ConversationHandler.END
        set_session_state(tg_id, "action", "reset_password")
        await msg.reply_text("Send your NEW password for website login.", reply_markup=ReplyKeyboardRemove())
        return RESET_NEW_PASSWORD

    # AUTHENTICATE flow
    if intent == "authenticate":
        if user:
            await user_model.update_telegram_mapping(db, phone, tg_id, tg_user.username)
            create_session(tg_id, phone, authed=True)
            await msg.reply_text("✅ Authentication successful! You can now add expenses.", reply_markup=ReplyKeyboardRemove())
            return ADD_QUERY
        else:
            set_session_state(tg_id, "action", "create_after_auth")
            await msg.reply_text("No account found. Send a password to create your website account.", reply_markup=ReplyKeyboardRemove())
            return ENTER_PASSWORD_CREATE

    # CREATE flow
    if intent == "create account":
        if user:
            await user_model.update_telegram_mapping(db, phone, tg_id, tg_user.username)
            create_session(tg_id, phone, authed=True)
            await msg.reply_text("Account already exists. Linked Telegram and authenticated.", reply_markup=ReplyKeyboardRemove())
            return ADD_QUERY
        else:
            set_session_state(tg_id, "action", "create")
            await msg.reply_text("Send a password to create your website account.", reply_markup=ReplyKeyboardRemove())
            return ENTER_PASSWORD_CREATE

# Receive password for account creation
async def receive_password_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw = (update.message.text or "").strip()
    if not pw:
        await update.message.reply_text("Password cannot be empty.")
        return ENTER_PASSWORD_CREATE

    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session:
        await update.message.reply_text("Session expired. Send /start.")
        return ConversationHandler.END

    phone = session.get("phone")
    db = context.application.bot_data.get("db")
    if db is None:
        await update.message.reply_text("Server unavailable.")
        return ConversationHandler.END

    user = await user_model.find_user_by_phone(db, phone)
    if user:
        await user_model.update_telegram_mapping(db, phone, tg_id, update.effective_user.username)
        create_session(tg_id, phone, authed=True)
        await update.message.reply_text("Account already existed — linked Telegram and authenticated.")
        return ADD_QUERY

    hashed =   hash_password(pw)
    await user_model.create_user(db, phone, hashed, name=update.effective_user.full_name)
    await user_model.update_telegram_mapping(db, phone, tg_id, update.effective_user.username)
    create_session(tg_id, phone, authed=True)
    await update.message.reply_text("Account created and linked to Telegram. You are authenticated.")
    return ADD_QUERY

# Receive new password for reset
async def receive_reset_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw = (update.message.text or "").strip()
    if not pw:
        await update.message.reply_text("Password cannot be empty.")
        return RESET_NEW_PASSWORD

    tg_id = update.effective_user.id
    session = get_session(tg_id)
    phone = session.get("phone")
    db = context.application.bot_data.get("db")
    user = await user_model.find_user_by_phone(db, phone)
    if not user:
        await update.message.reply_text("No account found for this phone.")
        destroy_session(tg_id)
        return ConversationHandler.END

    hashed =   hash_password(pw)
    await user_model.update_password_hash(db, phone, hashed)
    await update.message.reply_text("✅ Password updated.")
    destroy_session(tg_id)
    return ConversationHandler.END

# Add expense/query
async def add_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session or not session.get("authed"):
        await update.message.reply_text("Authenticate first. Send /start.")
        return ConversationHandler.END

    phone = session.get("phone")
    db = context.application.bot_data.get("db")

    # ---- Check for pending clarification ----
    pending = context.chat_data.get("pending")
    if pending:
        stage = pending.get("stage")
        parsed = pending.get("parsed", {})

        if stage == "await_name":
            # User just replied with the name
            name = (update.message.text or "").strip()
            if not name:
                await update.message.reply_text("Please send a valid name for the expense.")
                return ADD_QUERY

            parsed["name"] = name
            pending["stage"] = "await_price"
            context.chat_data["pending"] = pending
            await update.message.reply_text(
                f"Got it! Now, please enter the price for *{name}*.",
                parse_mode="Markdown"
            )
            return ADD_QUERY

        elif stage == "await_price":
            # User just replied with the price
            price_text = (update.message.text or "").strip()
            try:
                price = float(price_text.replace("₹", "").strip())
            except Exception:
                await update.message.reply_text("Couldn't parse the price. Please send a numeric value.")
                return ADD_QUERY

            parsed["price"] = price

            # Save to DB
            try:
                await user_model.create_query(
                    db,
                    phone_number=phone,
                    price=parsed.get("price", 0),
                    name=parsed.get("name", ""),
                    isIncome=parsed.get("isIncome", False),
                    category=parsed.get("category", "uncategorized"),
                    telegram_id=tg_id,
                )
            except Exception as e:
                await update.message.reply_text(f"Couldn't save expense: {e}")
                return ADD_QUERY

            await update.message.reply_text(
                f"📷 Expense saved!\n\n📝 {parsed.get('name','Unknown')}\n📁 {parsed.get('category','uncategorized')}\n💰 ₹{parsed.get('price',0)}",
                parse_mode="Markdown"
            )
            context.chat_data.pop("pending", None)
            return ADD_QUERY

    # ---- Photo ----
    if update.message.photo:
        try:
            await message_handle_image(update, context)
        except Exception as e:
            await update.message.reply_text(f"Failed to process image: {e}")
        return ADD_QUERY

    # ---- Text ----
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Send expense text (e.g., 'Lunch 120') or image with caption.")
        return ADD_QUERY

    # Normal text entry
    try:
        entry = parse_message_to_entry(text)

        # If name or price missing, start pending clarification
        name_missing = not entry.get("name")
        price_missing = not entry.get("price")

        if name_missing:
            context.chat_data["pending"] = {
                "stage": "await_name",
                "parsed": entry,
            }
            await update.message.reply_text("❓ I couldn't determine the name. Please type the name for this expense.")
            return ADD_QUERY

        if price_missing:
            context.chat_data["pending"] = {
                "stage": "await_price",
                "parsed": entry,
            }
            await update.message.reply_text(
                f"💰 I couldn't determine the price. Please enter the amount for *{entry.get('name','this expense')}*.",
                parse_mode="Markdown"
            )
            return ADD_QUERY

        # Save complete entry
        await user_model.create_query(
            db,
            phone_number=phone,
            price=entry.get("price", 0),
            name=entry.get("name", ""),
            isIncome=entry.get("isIncome", False),
            category=entry.get("category", "uncategorized"),
            telegram_id=tg_id,
        )
        await update.message.reply_text(
            f"✅ Expense saved!\n📝 {entry.get('name','Unknown')}\n📁 {entry.get('category','uncategorized')}\n💰 ₹{entry.get('price',0)}"
        )

    except Exception as e:
        await update.message.reply_text(f"Couldn't save expense: {e}")

    return ADD_QUERY

# Logout / cancel
async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    destroy_session(update.effective_user.id)
    await update.message.reply_text("Logged out.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Canceled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Build ConversationHandler
def build_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, choice_handler)],
            WAIT_CONTACT: [MessageHandler(filters.CONTACT, receive_contact)],
            ENTER_PASSWORD_CREATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password_create)],
            RESET_NEW_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reset_new_password)],
            ADD_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_query_handler),
                MessageHandler(filters.PHOTO, add_query_handler)
            ],
        },
        fallbacks=[CommandHandler("logout", logout), CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )