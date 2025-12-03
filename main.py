
# Standard library
import os
import sys
import json
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

# Third-party
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
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

# Local modules
from experiments.db_ops import add_query_for_user, normalize_to_10digits
from message_to_json import (
    categorization_with_confidence,
    needs_clarification,
    handle_text,
    handle_image,
    init_graph,
    download_image_base64,
)


# Load env
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# optional allow-list: comma separated phones, e.g. +919999999999,+911234567890
# AUTHORIZED_PHONES_ENV = os.getenv("AUTHORIZED_PHONES", "").strip()

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN not found in .env. Create a .env with TELEGRAM_BOT_TOKEN=your_token")
    sys.exit(1)


authenticated_users = {}


def normalize_phone(p: str) -> str:
    """
    Convert any Indian phone number into a clean 10-digit number.
    Examples:
      +91 9699585045 → 9699585045
      09699585045    → 9699585045
      +919699585045  → 9699585045
      91-9699585045  → 9699585045
    """
    if not p:
        return ""

    # remove spaces, hyphens, parentheses
    p = p.strip()
    for ch in " -()":
        p = p.replace(ch, "")

    # remove leading + sign
    if p.startswith("+"):
        p = p[1:]

    # remove leading country code 91
    if p.startswith("91") and len(p) > 10:
        p = p[2:]

    # remove leading 0 (common when dialing locally)
    if p.startswith("0") and len(p) > 10:
        p = p[1:]

    # final safety → keep only last 10 digits
    if len(p) > 10:
        p = p[-10:]

    return p



async def authenticate(contact_phone: str, contact_user_id: Optional[int], tg_user_id: int, db) -> bool:
    """
    Returns True only if:
      - contact_user_id is provided and equals tg_user_id (shared their own contact)
      - AND a user document exists in db.Users with number == normalized_10_digit_phone
    """
    # strong check: user must have shared their own contact
    if contact_user_id is None or contact_user_id != tg_user_id:
        return False

    if not contact_phone:
        return False

    norm = normalize_phone(contact_phone)
    if not norm or len(norm) != 10:
        return False

    # find user by 10-digit normalized number
    user = await db.Users.find_one({"number": norm})
    if not user:
        return False

    return True



async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for incoming contact shares.

    Requirements:
      - user must share their *own* contact (contact.user_id == sender id)
      - normalized 10-digit phone must already exist in db.Users.number
      - on success, set authenticated_users[tg_id] = <10-digit-phone>
      - persist telegram_id into the existing user document, *without* writing None into
        telegram_username (omit it if missing, or set "" if schema requires it).
    """
    msg = update.message
    if not msg:
        return

    contact = msg.contact
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)

    contact_phone = None
    contact_user_id = None

    if contact:
        contact_phone = contact.phone_number
        contact_user_id = contact.user_id   

    # fetch DB from app state
    db = context.bot_data.get("db")
    if db is None:
        await msg.reply_text("❌ Server DB not available. Contact admin.")
        return

    # Attempt authentication against DB (your authenticate must check DB existence)
    try:
        is_auth = await authenticate(contact_phone, contact_user_id, tg_id, db)
    except Exception as e:
        print("Error during authenticate():", e)
        await msg.reply_text("❌ Internal error during authentication. Try again later.")
        return

    # Log the attempt for debugging
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

    if not is_auth:
        await msg.reply_text(
            "❌ Authentication failed.\n"
            "You must press the *Share Phone Number* button and share *your own* contact.\n"
            "Also make sure your phone number exists in our system (we require pre-registered users).",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # ---- At this point user is authenticated ----
    norm_phone = normalize_phone(contact_phone)
    authenticated_users[tg_id] = norm_phone

    # Build $set fields but avoid writing None for telegram_username
    set_fields = {
        "telegram_id": tg_id,
        "name": f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip(),
        "updated_at": datetime.utcnow(),
    }

    # Only include telegram_username if it's actually present and a string
    if getattr(tg_user, "username", None):
        set_fields["telegram_username"] = tg_user.username
    else:
        # If your DB schema requires telegram_username to exist and be a string,
        # set it to empty string. Otherwise omit the field entirely (recommended).
        # set_fields["telegram_username"] = ""
        pass

    try:
        await db.Users.update_one(
            {"number": norm_phone},
            {"$set": set_fields},
            upsert=False,  # user must already exist
        )
    except Exception as e:
        # Log full exception: if schema validation fails you'll see details here
        print("Warning: failed to write telegram_id to user document:", e)
        # Do not fail the auth silently; still mark in-memory authenticated so UX is fine,
        # but message_handler will fail DB lookup until this is resolved.
        await msg.reply_text(
            "⚠️ Authentication succeeded locally but failed to persist link to the database. "
            "Contact admin if you face issues saving queries.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Optionally verify the update (helpful during dev)
    try:
        user_after = await db.Users.find_one({"number": norm_phone}, {"_id": 1, "number": 1, "telegram_id": 1, "telegram_username": 1})
        print("User after update (contact_handler):", user_after)
    except Exception:
        pass

    await msg.reply_text(
        "✅ Authentication successful!\nYour Telegram account is now linked to the stored phone number.",
        reply_markup=ReplyKeyboardRemove()
    )



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


def _normalize_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    # lowercase keys
    p = {str(k).strip().lower(): v for k, v in parsed.items()}
    # common alternates
    name = p.get("name") or p.get("item") or p.get("title") or p.get("Name")
    category = p.get("category") or p.get("type") or "Unknown"
    price_raw = p.get("price") or p.get("cost") or 0
    # cast price to numeric if possible
    try:
        if isinstance(price_raw, str):
            price_raw = price_raw.strip()
            # remove currency symbols if present
            price_raw = price_raw.replace("₹", "").replace("$", "").replace("rs", "").strip()
        if isinstance(price_raw, (int, float)):
            price_num = price_raw
        else:
            price_num = float(price_raw) if ('.' in str(price_raw)) else int(str(price_raw))
    except Exception:
        price_num = 0
    extra = p.get("extra", {})
    if not isinstance(extra, dict):
        extra = {"raw_extra": extra}
    return {"name": name, "category": category, "price": price_num, "extra": extra}


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Orchestrating message handler that:
      - ensures authentication,
      - attempts to parse & store queries for *new* messages (text/image) when confidence is high,
      - delegates to handle_text / handle_image for clarification flows,
      - detects when a pending flow is confirmed (handler pops pending) and then stores that parsed object.
    """
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)
    msg = update.message

    if msg is None:
        return

    # 1) auth check
    if tg_id not in authenticated_users:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="You are not authenticated. Send /start and press the 'Share Phone Number 📱' button to authenticate.",
            )
        except Exception as e:
            print("Warning: failed to send not-auth message:", e)
        return

    # Ensure DB available and fetch user record (we will need phone)
    db = context.bot_data.get("db")
    if db is None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Server DB not available. Contact admin.")
        return

    # try both int and str telegram_id lookups
    db_user = await db.Users.find_one({"telegram_id": tg_id}) or await db.Users.find_one({"telegram_id": str(tg_id)})
    if not db_user:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ User record not found. Please /start again to authenticate.")
        return

    phone = db_user.get("number") or db_user.get("phone") or db_user.get("mobile")
    if not phone:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ User record missing phone number. Please re-authenticate.")
        return

    # snapshot pending before calling handlers (to detect post-confirmation)
    prior_pending = context.chat_data.get("pending")

    # ---------- TEXT message path ----------
    if getattr(msg, "text", None):
        text = (msg.text or "").strip()

        # If no prior pending, attempt a quick auto-parse + store when confidence is high
        if not prior_pending:
            loop = asyncio.get_event_loop()
            # run model in executor (same function your handlers use)
            parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence(text, None))
            ask, issues = needs_clarification(parsed)
            if not ask:
                # accepted automatically -> normalize and store
                norm = _normalize_parsed(parsed)
                try:
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {})
                    )
                except Exception as e:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to store data: {e}")
                    print("add_query_for_user failed:", e)
                    return
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Saved your query! ID: {res.get('inserted_id')}")
                return
            else:
                # low-confidence -> defer to your existing clarify/verify flow
                await handle_text(update, context)
                # After user interaction, we may detect confirmation below
        else:
            # There is a pending flow -> delegate to handler (which will modify context.chat_data)
            await handle_text(update, context)

        # after handler returns, check if pending was present before and now gone => user confirmed
        new_pending = context.chat_data.get("pending")
        if prior_pending and (not new_pending):
            # user confirmed the earlier parsed object inside prior_pending
            parsed_confirmed = prior_pending.get("parsed")
            if parsed_confirmed:
                norm = _normalize_parsed(parsed_confirmed)
                try:
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {})
                    )
                except Exception as e:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to store confirmed data: {e}")
                    print("add_query_for_user failed on confirmed:", e)
                    return
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Saved confirmed query! ID: {res.get('inserted_id')}")
                return

        # nothing stored and flow handled by handler (clarifications etc.)
        return

    # ---------- IMAGE message path ----------
    elif getattr(msg, "photo", None):
        # If no prior pending, attempt a quick auto-parse using the image and store on high confidence
        if not prior_pending:
            # process image file to base64 (reuse your helper)
            photo = msg.photo
            file_id = photo[-1].file_id
            file_obj = await context.bot.get_file(file_id)
            try:
                image_b64 = await download_image_base64(file_obj)
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Failed to download image: {e}")
                return

            loop = asyncio.get_event_loop()
            parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence("", image_b64))
            ask, issues = needs_clarification(parsed)
            if not ask:
                norm = _normalize_parsed(parsed)
                try:
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {})
                    )
                except Exception as e:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to store image data: {e}")
                    print("add_query_for_user failed image:", e)
                    return
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Saved your query from image! ID: {res.get('inserted_id')}")
                return
            else:
                # low-confidence -> let your handler start clarification
                await handle_image(update, context)
        else:
            # there is prior pending -> delegate to handler (it will update/pop pending)
            await handle_image(update, context)

        # after handler returns, if prior_pending existed and now it's gone -> user confirmed
        new_pending = context.chat_data.get("pending")
        if prior_pending and (not new_pending):
            parsed_confirmed = prior_pending.get("parsed")
            if parsed_confirmed:
                norm = _normalize_parsed(parsed_confirmed)
                try:
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {})
                    )
                except Exception as e:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to store confirmed image data: {e}")
                    print("add_query_for_user failed on image confirmed:", e)
                    return
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Saved confirmed query from image! ID: {res.get('inserted_id')}")
                return

        return

    else:
        # unsupported type (but user is authenticated)
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Unsupported message type. Please send text or an image.")
        except Exception as e:
            print("Warning: failed to send unsupported-type message:", e)
        return












from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
# make sure BOT_TOKEN, start_command, contact_handler, message_handler are defined above
def main() -> None:
    # Ensure there is an event loop in Python 3.14
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # 🔧 Initialize the LangGraph graph BEFORE starting the bot
    init_graph()
    load_dotenv()

    MONGO_URI = os.getenv("MONGO_URI")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Finman")

    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB_NAME]
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.bot_data["db"] = db

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


