# bot_share_phone.py
import os
import sys
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from message_to_json import handle_text, handle_image, init_graph


# Load env
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# optional allow-list: comma separated phones, e.g. +919999999999,+911234567890
AUTHORIZED_PHONES_ENV = os.getenv("AUTHORIZED_PHONES", "").strip()

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN not found in .env. Create a .env with TELEGRAM_BOT_TOKEN=your_token")
    sys.exit(1)

# normalize allow list into a set (remove spaces)
AUTHORIZED_PHONES = set()
if AUTHORIZED_PHONES_ENV:
    for p in AUTHORIZED_PHONES_ENV.split(","):
        p = p.strip()
        if p:
            # basic normalization: ensure plus sign
            if not p.startswith("+") and p.isdigit():
                p = "+" + p
            AUTHORIZED_PHONES.add(p)

# In-memory authenticated users mapping: { telegram_id: phone_number }
authenticated_users = {}


def normalize_phone(p: str) -> str:
    """Normalize phone string for comparison: remove spaces, dashes, parentheses."""
    if not p:
        return ""
    p = p.strip()
    for ch in " -()":
        p = p.replace(ch, "")
    # ensure leading plus if looks like country+number (simple heuristic)
    if p and p[0].isdigit():
        # keep as-is or add plus? we won't force plus here as allow-list may include + or not
        pass
    return p


# def authenticate(contact_phone: str, contact_user_id: int | None, tg_user_id: int) -> bool:
#     """
#     Return True if user is authenticated based on:
#       1) contact_user_id matches sender tg_user_id (strong proof)
#       2) OR contact_phone appears in AUTHORIZED_PHONES (if set)
#     If AUTHORIZED_PHONES is empty, only method (1) will be accepted.
#     """
#     # 1) strong check: contact.user_id provided and matches sending user
#     if contact_user_id is not None and contact_user_id == tg_user_id:
#         return True

#     # 2) allow-list check (if configured)
#     if AUTHORIZED_PHONES:
#         norm = normalize_phone(contact_phone)
#         # allow comparison in two common formats: with and without +
#         candidates = {norm, ("+" + norm) if not norm.startswith("+") else norm.replace("+", "")}
#         # create normalized authorized set for comparison
#         auth_normalized = {normalize_phone(x).lstrip("+") for x in AUTHORIZED_PHONES}
#         # compare without leading plus
#         if normalize_phone(norm).lstrip("+") in auth_normalized or (("+" + normalize_phone(norm)).lstrip("+") in auth_normalized):
#             return True

#     return False
def authenticate(contact_phone: str, contact_user_id: int | None, tg_user_id: int) -> bool:
    """
    Authenticate ONLY if the user shared their own Telegram contact.
    That means contact.user_id must equal the message sender's Telegram ID.
    """
    # Strong and only check: contact.user_id must match their own Telegram ID.
    if contact_user_id is not None and contact_user_id == tg_user_id:
        return True

    # Otherwise reject (shared someone else's contact or typed phone manually)
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start handler: asks the user to share their phone number using the request_contact keyboard button.
    """
    # print("start command Called")
    button = KeyboardButton(text="Share Phone Number 📱", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[button]], one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "Hi! To authenticate please share your phone number by pressing the button below.",
        reply_markup=keyboard,
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    contact = msg.contact
    tg_user = update.effective_user
    tg_id = tg_user.id

    contact_phone = None
    contact_user_id = None

    if contact:
        contact_phone = contact.phone_number
        contact_user_id = contact.user_id   # IMPORTANT field

    # authenticate
    is_auth = authenticate(contact_phone, contact_user_id, tg_id)

    # For debugging / logging
    printed = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "action": "contact_received_and_auth_attempt",
        "telegram_user": {
            "id": tg_id,
            "username": tg_user.username,
            "first_name": tg_user.first_name,
            "last_name": tg_user.last_name,
        },
        "contact_shared": {
            "phone_number": contact_phone,
            "contact_user_id": contact_user_id,
        },
        "authenticated": is_auth,
    }

    print("\n" + "=" * 28 + " AUTH ATTEMPT " + "=" * 28)
    print(json.dumps(printed, indent=2))
    print("=" * 72 + "\n")

    # If authenticated, save user & acknowledge
    if is_auth:
        authenticated_users[tg_id] = normalize_phone(contact_phone)

        await msg.reply_text(
            "✅ Authentication successful!\nYou shared *your own* contact.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await msg.reply_text(
            "❌ Authentication failed.\n"
            "You must press the *Share Phone Number* button and share *your own* contact.\n"
            "Forwarding someone else’s contact will NOT work.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any non-contact update (text/media).
    If user is authenticated, delegate to handle_text / handle_image.
    Otherwise prompt them to /start and share contact.
    """
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)
    msg = update.message

    # No message object (can happen for some update types) → nothing to do
    if msg is None:
        return

    # 1️⃣ Check authentication first
    if tg_id not in authenticated_users:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="You are not authenticated. Send /start and press the 'Share Phone Number 📱' button to authenticate.",
            )
        except Exception as e:
            print("Warning: failed to send not-auth message:", e)
        return

    # 2️⃣ User is authenticated → route by content type
    if getattr(msg, "text", None):
        # delegate to your text handler from message_to_json.py
        await handle_text(update, context)

    elif getattr(msg, "photo", None):
        # delegate to your image handler from message_to_json.py
        await handle_image(update, context)

    else:
        # unsupported message type, but user *is* authenticated
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Unsupported message type. Please send text or an image."
            )
        except Exception as e:
            print("Warning: failed to send unsupported-type message:", e)

# make sure BOT_TOKEN, start_command, contact_handler, message_handler
# are already defined above thisimport asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
# make sure BOT_TOKEN, start_command, contact_handler, message_handler are defined above
def main() -> None:
    # Ensure there is an event loop in Python 3.14
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 🔧 Initialize the LangGraph graph BEFORE starting the bot
    init_graph()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.CONTACT, message_handler))

    print("Bot is starting (polling). Ask a user to /start and share contact.")

    app.run_polling(
        poll_interval=1.0,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
