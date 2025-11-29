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


def authenticate(contact_phone: str, contact_user_id: int | None, tg_user_id: int) -> bool:
    """
    Return True if user is authenticated based on:
      1) contact_user_id matches sender tg_user_id (strong proof)
      2) OR contact_phone appears in AUTHORIZED_PHONES (if set)
    If AUTHORIZED_PHONES is empty, only method (1) will be accepted.
    """
    # 1) strong check: contact.user_id provided and matches sending user
    if contact_user_id is not None and contact_user_id == tg_user_id:
        return True

    # 2) allow-list check (if configured)
    if AUTHORIZED_PHONES:
        norm = normalize_phone(contact_phone)
        # allow comparison in two common formats: with and without +
        candidates = {norm, ("+" + norm) if not norm.startswith("+") else norm.replace("+", "")}
        # create normalized authorized set for comparison
        auth_normalized = {normalize_phone(x).lstrip("+") for x in AUTHORIZED_PHONES}
        # compare without leading plus
        if normalize_phone(norm).lstrip("+") in auth_normalized or (("+" + normalize_phone(norm)).lstrip("+") in auth_normalized):
            return True

    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start handler: asks the user to share their phone number using the request_contact keyboard button.
    """
    button = KeyboardButton(text="Share Phone Number 📱", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[button]], one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "Hi! To authenticate please share your phone number by pressing the button below.",
        reply_markup=keyboard,
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when the user shares a contact.
    Authenticate the user and add to in-memory authenticated_users if successful.
    """
    msg = update.message
    contact = msg.contact  # telegram.Contact or None
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)

    contact_phone = None
    contact_user_id = None

    if contact:
        contact_phone = getattr(contact, "phone_number", None)
        contact_user_id = getattr(contact, "user_id", None)

    # Try to authenticate
    is_auth = authenticate(contact_phone, contact_user_id, tg_id)

    printed = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "action": "contact_received_and_auth_attempt",
        "telegram_user": {
            "id": tg_id,
            "username": getattr(tg_user, "username", None),
            "first_name": getattr(tg_user, "first_name", None),
            "last_name": getattr(tg_user, "last_name", None),
        },
        "contact_shared": {
            "phone_number": contact_phone,
            "contact_user_id": contact_user_id,
            "vcard": getattr(contact, "vcard", None) if contact else None,
        },
        "authenticated": is_auth,
    }

    print("\n" + "=" * 28 + " AUTH ATTEMPT " + "=" * 28)
    print(json.dumps(printed, indent=2, ensure_ascii=False))
    print("=" * 72 + "\n")

    if is_auth:
        # store in in-memory authenticated map
        # normalize phone for storage
        stored_phone = normalize_phone(contact_phone) if contact_phone else None
        authenticated_users[tg_id] = stored_phone
        try:
            await msg.reply_text(
                "✅ Authentication successful. You are now authenticated. Send any message and it will be printed on the server.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            print("Warning: failed to reply after auth:", e)
    else:
        # remove keyboard and inform
        try:
            await msg.reply_text(
                "❌ Authentication failed. Make sure you shared your own contact or your phone is authorized.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            print("Warning: failed to reply after failed auth:", e)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any non-contact update (text/media). If user is authenticated, print the same message in terminal.
    Otherwise prompt them to /start and share contact.
    """
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)
    msg = update.message

    # Get message text representation: for non-text we show a short description
    if msg is None:
        msg_repr = "<non-message update>"
    else:
        if getattr(msg, "text", None):
            msg_repr = msg.text
        elif getattr(msg, "photo", None):
            msg_repr = "<photo>"
        elif getattr(msg, "voice", None):
            msg_repr = "<voice>"
        elif getattr(msg, "audio", None):
            msg_repr = "<audio>"
        elif getattr(msg, "document", None):
            msg_repr = "<document>"
        elif getattr(msg, "location", None):
            msg_repr = "<location>"
        else:
            msg_repr = "<other message type>"

    if tg_id in authenticated_users:
        # print same message in terminal along with user info
        printed = {
            "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            "action": "authenticated_message_print",
            "telegram_user": {
                "id": tg_id,
                "username": getattr(tg_user, "username", None),
                "first_name": getattr(tg_user, "first_name", None),
                "last_name": getattr(tg_user, "last_name", None),
            },
            "authenticated_phone_stored": authenticated_users.get(tg_id),
            "message_sent_by_user": msg_repr,
        }
        print("\n" + "=" * 30 + " AUTHENTICATED MESSAGE " + "=" * 30)
        print(json.dumps(printed, indent=2, ensure_ascii=False))
        print("=" * 78 + "\n")

        # Acknowledge to user
        try:
            if msg and getattr(msg, "text", None):
                await msg.reply_text("✅ Message printed to server terminal.")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Update printed to server terminal.")
        except Exception as e:
            print("Warning: failed to send acknowledgement:", e)
    else:
        # Not authenticated — instruct them to /start and share contact
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="You are not authenticated. Send /start and press the 'Share Phone Number' button to authenticate.",
            )
        except Exception as e:
            print("Warning: failed to send not-auth message:", e)


def main():
    # fresh event loop to avoid common asyncio issues
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start_command))

    # contact shares
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    # all other messages (text/media) — handle after contact handler
    app.add_handler(MessageHandler(filters.ALL & ~filters.CONTACT, message_handler))

    print("Bot is starting (polling). Ask a user to /start and share contact.")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        print("Bot stopped due to exception:", e)
    finally:
        try:
            loop.run_until_complete(app.shutdown())
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
