# bot/auth_handlers.py
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
# delegate image pipeline
from message_to_json import handle_image as message_handle_image
from message_to_json import parse_message_to_entry

from typing import Optional
from datetime import datetime

from bot import user_model
from bot.sessions import create_session, get_session, set_session_state, destroy_session
from utils.phone_utils import normalize_phone
from utils.crypto import hash_password  # async password hasher

# Conversation states
CHOOSING, ENTER_CONTACT_OR_PHONE, ENTER_PASSWORD_CREATE, ADD_QUERY, RESET_NEW_PASSWORD = range(5)


def main_menu_kb():
    """Main menu — Reset Password is always shown here."""
    return ReplyKeyboardMarkup(
        [["Create account", "Authenticate"], ["Reset password"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


def auth_menu_kb():
    """Prompt for share contact (used for create/auth/reset prompts)."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Share contact", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point showing main menu."""
    await update.message.reply_text("Welcome — choose an option:", reply_markup=main_menu_kb())
    context.user_data.pop("intent", None)
    return CHOOSING


async def choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles menu choice and sets intent for next step."""
    text = (update.message.text or "").strip().lower()

    # accept common variants for reset wording (button text vs underscore vs typed)
    if text == "create account":
        await update.message.reply_text(
            "Share your contact (recommended) or type your phone number to create an account. You will be asked for a website password.",
            reply_markup=auth_menu_kb(),
        )
        context.user_data["intent"] = "create"
        return ENTER_CONTACT_OR_PHONE

    if text == "authenticate":
        await update.message.reply_text(
            "Share your contact to authenticate (no password required if this is your own contact).",
            reply_markup=auth_menu_kb(),
        )
        context.user_data["intent"] = "authenticate"
        return ENTER_CONTACT_OR_PHONE

    # handle the Reset option from the keyboard (covers "Reset password", "reset password",
    # "reset_password" and users who type "reset")
    if text in ("reset_password", "reset password", "reset"):
        await update.message.reply_text(
            "Share your contact (recommended) or type your phone number to reset your website password.",
            reply_markup=auth_menu_kb(),
        )
        context.user_data["intent"] = "reset"
        return ENTER_CONTACT_OR_PHONE

    await update.message.reply_text("Please choose a valid option.", reply_markup=main_menu_kb())
    return CHOOSING



async def handle_image_in_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Wrapper that only allows authenticated users (via session) to use the
    image pipeline in message_to_json.handle_image.
    """
    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session or not session.get("authed"):
        await update.message.reply_text("You are not authenticated. Send /start and press the 'Share Phone Number' 📱 button to authenticate.")
        return ConversationHandler.END

    phone = session.get("phone")
    if not phone:
        await update.message.reply_text("Session missing phone data. Send /start and share contact again.")
        destroy_session(tg_id)
        return ConversationHandler.END

    try:
        # delegate to the existing pipeline (downloads image, runs model, stores)
        await message_handle_image(update, context)
    except Exception as e:
        print("Error in handle_image_in_auth delegating to message_to_json:", e)
        try:
            await update.message.reply_text(f"Failed to process image: {e}")
        except Exception:
            pass
    return ADD_QUERY



# Add this to bot/auth_handlers.py

async def reset_password_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Starts the Reset Password flow (same as choosing Reset from the menu).
    This asks the user to share contact (auth_menu_kb) and sets intent to 'reset'.
    Returns ENTER_CONTACT_OR_PHONE so the same receive_contact_or_phone handler takes over.
    """
    # Keep menu consistent and set the flow intent
    context.user_data["intent"] = "reset"

    await update.message.reply_text(
        "Share your contact (recommended) or type your phone number to reset your website password.",
        reply_markup=auth_menu_kb(),
    )

    return ENTER_CONTACT_OR_PHONE





async def receive_contact_or_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    STRICT: only accept a shared contact that is the sender's own contact.
    We do NOT accept typed phone numbers for security (those could be anyone's).
    """
    msg = update.message
    tg_user = update.effective_user
    tg_id = tg_user.id

    # Must be a contact share. Reject plain typed numbers.
    contact = msg.contact
    if contact is None:
        # Reject typed numbers — require Share Contact button for safety.
        await msg.reply_text(
            "For security we require you to *share your contact* using the Share Contact button 📱.\n"
            "Please press 'Share contact' and choose your own contact. Typed phone numbers are not allowed.",
            parse_mode="Markdown",
        )
        return ENTER_CONTACT_OR_PHONE

    # If contact.user_id exists, it should match the sender.
    # Some clients may set contact.user_id to None — in that case also reject (force Share Contact button).
    contact_user_id = getattr(contact, "user_id", None)
    if contact_user_id is None:
        await msg.reply_text(
            "Please use the Share Contact button (it identifies the contact as yours). "
            "If your client didn't attach the contact user id, try using the button again.",
            parse_mode="Markdown",
        )
        return ENTER_CONTACT_OR_PHONE

    # If the shared contact belongs to someone else, reject.
    if int(contact_user_id) != int(tg_id):
        await msg.reply_text(
            "Please share *your own* contact using the Share Contact button (not someone else's).",
            parse_mode="Markdown",
        )
        return ENTER_CONTACT_OR_PHONE

    # At this point the contact is the user's own contact and is safe to use.
    raw_phone = contact.phone_number
    phone = normalize_phone(raw_phone)
    if not phone:
        await msg.reply_text("The shared contact's phone number looks invalid. Please share a valid contact.")
        return ENTER_CONTACT_OR_PHONE

    # Save phone in session (used across states)
    create_session(tg_id, phone, authed=False)
    set_session_state(tg_id, "phone", phone)

    db = context.application.bot_data.get("db")
    if db is None:
        await msg.reply_text("Server DB not available. Try again later.", reply_markup=ReplyKeyboardRemove())
        destroy_session(tg_id)
        return ConversationHandler.END

    user = await user_model.find_user_by_phone(db, phone)

    intent = (context.user_data.get("intent") or "").lower()
    if not intent:
        intent = "authenticate" if user else "create"

    # ------------------- RESET FLOW -------------------
    if intent == "reset":
        if not user:
            await msg.reply_text("No account found for this number. Use 'Create account' to register.", reply_markup=ReplyKeyboardRemove())
            destroy_session(tg_id)
            return ConversationHandler.END

        set_session_state(tg_id, "action", "reset_password")
        await msg.reply_text("Please send the NEW password you'd like to use for the website (this will replace the existing website password).", reply_markup=ReplyKeyboardRemove())
        return RESET_NEW_PASSWORD

    # ------------------- AUTHENTICATE FLOW -------------------
    if intent == "authenticate":
        if user:
            try:
                await user_model.update_telegram_mapping(db, phone, tg_id, tg_user.username)
            except Exception:
                pass
            create_session(tg_id, phone, authed=True)
            await msg.reply_text("✅ Authentication successful — your Telegram is now linked to your account. You can add expenses now.", reply_markup=ReplyKeyboardRemove())
            return ADD_QUERY
        else:
            set_session_state(tg_id, "action", "create_after_auth")
            await msg.reply_text("No account found for this number. If you'd like to create a website account now, send a password to use for website login.", reply_markup=ReplyKeyboardRemove())
            return ENTER_PASSWORD_CREATE

    # ------------------- CREATE FLOW -------------------
    if intent == "create":
        if user:
            try:
                await user_model.update_telegram_mapping(db, phone, tg_id, tg_user.username)
            except Exception:
                pass
            create_session(tg_id, phone, authed=True)
            await msg.reply_text("An account already exists for this number. Linked your Telegram and authenticated you.", reply_markup=ReplyKeyboardRemove())
            return ADD_QUERY
        else:
            set_session_state(tg_id, "action", "create")
            await msg.reply_text("No account found. Please send a password to create your website account (this password will be used for website login).", reply_markup=ReplyKeyboardRemove())
            return ENTER_PASSWORD_CREATE



async def receive_password_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accepts password to create website account (or used when authenticate found no user).
    After creation, links Telegram and sets session authed.
    """
    pw = (update.message.text or "").strip()
    if not pw:
        await update.message.reply_text("Password cannot be empty. Send a valid password.")
        return ENTER_PASSWORD_CREATE

    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session:
        await update.message.reply_text("Session expired. Send /start.")
        return ConversationHandler.END

    phone = session.get("phone")
    if not phone:
        await update.message.reply_text("Phone missing. Send /start and share your contact again.")
        return ConversationHandler.END

    db = context.application.bot_data.get("db")
    if db is None:
        await update.message.reply_text("Server DB not available.")
        return ConversationHandler.END

    existing = await user_model.find_user_by_phone(db, phone)
    if existing:
        try:
            await user_model.update_telegram_mapping(db, phone, tg_id, update.effective_user.username)
        except Exception:
            pass
        create_session(tg_id, phone, authed=True)
        await update.message.reply_text("Account already existed — linked your Telegram and authenticated you.")
        return ADD_QUERY

    hashed = await hash_password(pw)
    try:
        created = await user_model.create_user(db, phone, hashed, name=update.effective_user.full_name)
    except Exception as e:
        await update.message.reply_text(f"Failed to create account: {e}")
        destroy_session(tg_id)
        return ConversationHandler.END

    try:
        await user_model.update_telegram_mapping(db, phone, tg_id, update.effective_user.username)
    except Exception:
        pass
    create_session(tg_id, phone, authed=True)
    await update.message.reply_text("Account created for website and linked to this Telegram. You are authenticated for bot usage.")
    return ADD_QUERY


async def receive_reset_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accepts the new password right after user chose RESET_PASSWORD and shared contact/phone.
    Immediately hashes and updates DB.
    """
    pw = (update.message.text or "").strip()
    if not pw:
        await update.message.reply_text("Password cannot be empty. Send a valid password.")
        return RESET_NEW_PASSWORD

    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session:
        await update.message.reply_text("Session expired. Send /start.")
        return ConversationHandler.END

    phone = session.get("phone")
    if not phone:
        await update.message.reply_text("Phone missing. Send /start and share contact again.")
        return ConversationHandler.END

    db = context.application.bot_data.get("db")
    if db is None:
        await update.message.reply_text("Server DB not available.")
        return ConversationHandler.END

    user = await user_model.find_user_by_phone(db, phone)
    if not user:
        await update.message.reply_text("No account found for this phone. Use Create account to make a website account.", reply_markup=main_menu_kb())
        destroy_session(tg_id)
        return ConversationHandler.END

    hashed = await hash_password(pw)
    try:
        await user_model.update_password_hash(db, phone, hashed)
    except Exception as e:
        await update.message.reply_text(f"Failed to update password: {e}")
        destroy_session(tg_id)
        return ConversationHandler.END

    await update.message.reply_text("✅ Password updated for website login. You can now use the website to log in with this password.")
    destroy_session(tg_id)
    return ConversationHandler.END


async def add_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Save an expense/query after the user is authenticated via Telegram.
    Accepts text messages or photos with captions. For full LLM-driven image
    understanding use the image pipeline (we added a handler for it).
    """
    tg_id = update.effective_user.id
    session = get_session(tg_id)
    if not session or not session.get("authed"):
        await update.message.reply_text("You must authenticate first. Send /start and share your contact.")
        return ConversationHandler.END

    phone = session.get("phone")

    # If message is a photo, take the caption, otherwise take text
    if update.message.photo:
        text = (update.message.caption or "").strip()
        if not text:
            await update.message.reply_text("Please add a caption describing the item or price, or send a text message.")
            return ADD_QUERY
    else:
        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text("Send the expense text (e.g., 'Lunch 120 Food') or an image with caption.")
            return ADD_QUERY

    # Parse the text into entry (synchronous helper that calls LLM if needed)
    try:
        entry = parse_message_to_entry(text)
    except Exception as e:
        await update.message.reply_text(f"Couldn't parse message: {e}")
        return ADD_QUERY

    db = context.application.bot_data.get("db")
    if db is None:
        await update.message.reply_text("🔌 Oops! We're having trouble connecting to our servers.\nPlease try again shortly.")
        return ConversationHandler.END

    try:
        inserted_id = await user_model.create_query(
            db,
            phone_number=phone,
            price=entry.get("price", 0),
            name=entry.get("name", ""),
            category=entry.get("category", "uncategorized"),
            isIncome=entry.get("isIncome", False),
            telegram_id=tg_id,
        )
    except Exception as e:
        await update.message.reply_text("❌ *Couldn't save your expense.*\n\nPlease try again in a moment.", parse_mode="Markdown")
        return ADD_QUERY

    name = entry.get("name", "Unknown")
    category = entry.get("category", "uncategorized")
    price = entry.get("price", 0)
    await update.message.reply_text(
        f"✅ *Expense saved!*\n\n"
        f"📝 *{name}*\n"
        f"📁 Category: {category}\n"
        f"💰 Amount: ₹{price}",
        parse_mode="Markdown"
    )
    return ADD_QUERY


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    destroy_session(tg_id)
    await update.message.reply_text("Logged out (session cleared).")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Canceled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def build_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, choice_handler)],
            ENTER_CONTACT_OR_PHONE: [MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, receive_contact_or_phone)],
            ENTER_PASSWORD_CREATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password_create)],
            RESET_NEW_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reset_new_password)],
            ADD_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_query_handler),
                MessageHandler(filters.PHOTO, handle_image_in_auth),  # photo delegation to LLM pipeline
            ],
        },
        fallbacks=[CommandHandler("logout", logout), CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
