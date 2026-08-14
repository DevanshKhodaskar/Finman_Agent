# main.py
"""
Replacement main.py — combines your previous handlers with the new auth conversation
and safer graph initialization. Based on your original main.py. :contentReference[oaicite:1]{index=1}
"""
import os
import sys
import json
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

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

# Try to import graph creators / handlers from your modules
# Prefer langchain_bot.create_graph, then message_to_json.init_graph
try:
    from langchain_bot import create_graph as langchain_create_graph
except Exception:
    langchain_create_graph = None

try:
    # message_to_json in your repo contains various helpers and may expose init_graph
    from message_to_json import (
        categorization_with_confidence,
        needs_clarification,
        handle_text,
        handle_image,
        init_graph as message_init_graph,
        download_image_base64,
    )
except Exception:
    categorization_with_confidence = None
    needs_clarification = None
    handle_text = None
    handle_image = None
    message_init_graph = None
    download_image_base64 = None

# Import auth conversation if present
try:
    from bot.auth_handlers import build_handler as build_auth_handler
except Exception:
    build_auth_handler = None

# Load env
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_TESTING_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Finman")

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN not found in .env. Create a .env with TELEGRAM_TESTING_BOT_TOKEN=your_token")
    sys.exit(1)

if not MONGO_URI:
    print("ERROR: MONGO_URI not found in .env. Create a .env with MONGO_URI=your_mongo_uri")
    sys.exit(1)

# -------------------------
# In-memory auth map (keeps same behavior as your original main)
authenticated_users: Dict[int, str] = {}

# -------------------------
# Helpers copied/adapted from your original main.py
def normalize_phone(p: str) -> str:
    """
    Convert any Indian phone number into a clean 10-digit number.
    Examples:
      +91 9699585045 -> 9699585045
      09699585045    -> 9699585045
      +919699585045  -> 9699585045
    """
    if not p:
        return ""
    p = p.strip()
    for ch in " -()":
        p = p.replace(ch, "")
    if p.startswith("+"):
        p = p[1:]
    if p.startswith("91") and len(p) > 10:
        p = p[2:]
    if p.startswith("0") and len(p) > 10:
        p = p[1:]
    if len(p) > 10:
        p = p[-10:]
    return p

async def authenticate(contact_phone: str, contact_user_id: Optional[int], tg_user_id: int, db) -> bool:
    """
    Returns True only if:
      - contact_user_id is provided and equals tg_user_id (shared their own contact)
      - AND a user document exists in db.users with number == normalized_10_digit_phone
    """
    if contact_user_id is None or contact_user_id != tg_user_id:
        return False
    if not contact_phone:
        return False
    norm = normalize_phone(contact_phone)
    if not norm or len(norm) != 10:
        return False
    user = await db.users.find_one({"phone_number": norm})
    if not user:
        return False
    return True

# ---------- Handlers (copied/adapted from your original main.py) ----------
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    db = context.bot_data.get("db")
    if db is None:
        await msg.reply_text("🔌 Oops! We're having trouble connecting to our servers.\nPlease try again in a few minutes or contact support.")
        return

    try:
        is_auth = await authenticate(contact_phone, contact_user_id, tg_id, db)
    except Exception as e:
        print("Error during authenticate():", e)
        await msg.reply_text("⚠️ Something went wrong during authentication.\nPlease wait a moment and try again.")
        return

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
            "❌ *Authentication Failed*\n\n"
            "This could happen because:\n"
            "• You didn't use the *Share Phone Number* button\n"
            "• You shared someone else's contact instead of your own\n"
            "• Your phone number isn't registered in our system yet\n\n"
            "💡 *Tip:* Press the button below and select your own contact to try again.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    norm_phone = normalize_phone(contact_phone)
    authenticated_users[tg_id] = norm_phone

    set_fields = {
        "telegram_id": tg_id,
        "name": f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip(),
        "updated_at": datetime.utcnow(),
    }
    if getattr(tg_user, "username", None):
        set_fields["telegram_username"] = tg_user.username

    try:
        await db.users.update_one({"phone_number": norm_phone}, {"$set": set_fields}, upsert=False)
    except Exception as e:
        print("Warning: failed to write telegram_id to user document:", e)
        await msg.reply_text(
            "⚠️ *Partial Success*\n\n"
            "You're authenticated, but we had a small hiccup saving your details.\n"
            "Don't worry — you can still use the bot! If you run into issues, just contact support.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    try:
        user_after = await db.users.find_one({"phone_number": norm_phone}, {"_id": 1, "phone_number": 1, "telegram_id": 1, "telegram_username": 1})
        print("User after update (contact_handler):", user_after)
    except Exception:
        pass

    await msg.reply_text(
        "🎉 *You're all set!*\n\n"
        "Your Telegram account is now linked to your phone number.\n"
        "You can start tracking expenses by sending text or images!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    button = KeyboardButton(text="Share Phone Number 📱", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[button]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        "👋 *Welcome to Budget Manager!*\n\n"
        "To get started, please verify your phone number by tapping the button below.\n\n"
        "📱 This helps us keep your expenses secure and linked to your account.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

def _normalize_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    p = {str(k).strip().lower(): v for k, v in parsed.items()}
    name = p.get("name") or p.get("item") or p.get("title") or p.get("Name")
    category = p.get("category") or p.get("type") or "Unknown"
    price_raw = p.get("price") or p.get("cost") or 0
    try:
        if isinstance(price_raw, str):
            price_raw = price_raw.strip()
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

# message_handler delegating to your existing model/handlers (copied/adapted)
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    tg_id = getattr(tg_user, "id", None)
    msg = update.message
    if msg is None:
        return

    if tg_id not in authenticated_users:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="""🌟 *Welcome to FinMan!*  
Your personal finance buddy 🤝💜

Here’s your quick menu:

🆕 *Create Account*  
🔐 *Authenticate*  
🔄 *Reset Password*  
📊 *Dashboard*

⚠️ *Please authenticate first!*  
Send */start* and tap the 📱 *Share Phone Number* button.

🔄 To view this menu anytime, just type /start.

Let’s manage your money smarter together 🚀💰""",
                parse_mode="Markdown"
            )
        except Exception as e:
            print("Warning: failed to send not-auth message:", e)
        return


    db = context.bot_data.get("db")
    if db is None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🔌 Oops! We're having trouble connecting to our servers.\nPlease try again shortly.")
        return

    db_user = await db.users.find_one({"telegram_id": tg_id}) or await db.users.find_one({"telegram_id": str(tg_id)})
    if not db_user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔍 *Hmm, we couldn't find your account.*\n\nPlease send /start to authenticate again.",
            parse_mode="Markdown"
        )
        return

    phone = db_user.get("phone_number") or db_user.get("phone") or db_user.get("mobile")
    if not phone:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ *Account issue detected.*\n\nYour account is missing phone info. Please send /start to re-authenticate.",
            parse_mode="Markdown"
        )
        return

    prior_pending = context.chat_data.get("pending")

    # TEXT
    if getattr(msg, "text", None):
        text = (msg.text or "").strip()

        if not prior_pending:
            loop = asyncio.get_event_loop()
            if categorization_with_confidence:
                parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence(text, None))
                ask, issues = needs_clarification(parsed) if needs_clarification else (True, [])
            else:
                parsed = {}
                ask, issues = (True, [])

            if not ask:
                norm = _normalize_parsed(parsed)
                try:
                    # add_query_for_user is in experiments.db_ops (keep original behavior)
                    from experiments.db_ops import add_query_for_user
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {}),
                    )
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ *Couldn't save your expense.*\n\nPlease try again in a moment.",
                        parse_mode="Markdown"
                    )
                    print("add_query_for_user failed:", e)
                    return
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ *Expense saved!*\n\n"
                         f"📝 *{norm['name']}*\n"
                         f"📁 Category: {norm['category']}\n"
                         f"💰 Amount: ₹{norm['price']}",
                    parse_mode="Markdown"
                )
                return
            else:
                if handle_text:
                    await handle_text(update, context)
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="🤔 *Need more details!*\n\nPlease provide additional info about this expense.",
                        parse_mode="Markdown"
                    )
        else:
            if handle_text:
                await handle_text(update, context)
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🤔 *Need more details!*\n\nPlease provide additional info about this expense.",
                    parse_mode="Markdown"
                )

        new_pending = context.chat_data.get("pending")
        if prior_pending and (not new_pending):
            parsed_confirmed = prior_pending.get("parsed")
            if parsed_confirmed:
                norm = _normalize_parsed(parsed_confirmed)
                try:
                    from experiments.db_ops import add_query_for_user
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {}),
                    )
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ *Couldn't save your expense.*\n\nPlease try again in a moment.",
                        parse_mode="Markdown"
                    )
                    print("add_query_for_user failed on confirmed:", e)
                    return
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ *Expense confirmed & saved!*\n\n"
                         f"📝 *{norm['name']}*\n"
                         f"📁 Category: {norm['category']}\n"
                         f"💰 Amount: ₹{norm['price']}",
                    parse_mode="Markdown"
                )
                return
        return

    # IMAGE
    elif getattr(msg, "photo", None):
        caption = (msg.caption or "").strip()

        if not prior_pending:
            photo = msg.photo
            file_id = photo[-1].file_id
            file_obj = await context.bot.get_file(file_id)
            try:
                image_b64 = await download_image_base64(file_obj) if download_image_base64 else None
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="📷 *Couldn't process your image.*\n\nPlease try sending it again or use a smaller image.",
                    parse_mode="Markdown"
                )
                return

            loop = asyncio.get_event_loop()
            if categorization_with_confidence:
                parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence(caption, image_b64))
                ask, issues = needs_clarification(parsed) if needs_clarification else (True, [])
            else:
                parsed = {}
                ask, issues = (True, [])

            if not ask:
                norm = _normalize_parsed(parsed)
                try:
                    from experiments.db_ops import add_query_for_user
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {}),
                    )
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ *Couldn't save your expense.*\n\nPlease try again in a moment.",
                        parse_mode="Markdown"
                    )
                    print("add_query_for_user failed image:", e)
                    return
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"📷 *Expense from image saved!*\n\n"
                         f"📝 *{norm['name']}*\n"
                         f"📁 Category: {norm['category']}\n"
                         f"💰 Amount: ₹{norm['price']}",
                    parse_mode="Markdown"
                )
                return
            else:
                if handle_image:
                    await handle_image(update, context)
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="🤔 *Need more details about this image!*\n\nPlease add a caption describing the item and price.",
                        parse_mode="Markdown"
                    )
        else:
            if handle_image:
                await handle_image(update, context)
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🤔 *Need more details about this image!*\n\nPlease add a caption describing the item and price.",
                    parse_mode="Markdown"
                )

        new_pending = context.chat_data.get("pending")
        if prior_pending and (not new_pending):
            parsed_confirmed = prior_pending.get("parsed")
            if parsed_confirmed:
                norm = _normalize_parsed(parsed_confirmed)
                try:
                    from experiments.db_ops import add_query_for_user
                    res = await add_query_for_user(
                        db=db,
                        number=phone,
                        name=norm["name"] or "Unknown",
                        category=norm["category"] or "Unknown",
                        price=norm["price"] or 0,
                        time=datetime.utcnow(),
                        extra=norm.get("extra", {}),
                    )
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="❌ *Couldn't save your expense.*\n\nPlease try again in a moment.",
                        parse_mode="Markdown"
                    )
                    print("add_query_for_user failed on image confirmed:", e)
                    return
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"📷 *Expense confirmed & saved!*\n\n"
                         f"📝 *{norm['name']}*\n"
                         f"📁 Category: {norm['category']}\n"
                         f"💰 Amount: ₹{norm['price']}",
                    parse_mode="Markdown"
                )
                return
        return

    else:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🤷 *I can only process text or images.*\n\n"
                     "Try sending:\n"
                     "• A text message like \"Coffee ₹50\"\n"
                     "• A photo of a receipt or bill",
                parse_mode="Markdown"
            )
        except Exception as e:
            print("Warning: failed to send unsupported-type message:", e)
        return
# ensure indexes for strict schema enforcement
async def _create_indexes(db):
    """
    Create necessary indexes. Safe to call repeatedly.
    - number: unique (users)
    - phone_number: non-unique index for queries (optional)
    """
    try:
        # create unique index on users.number
        await db.users.create_index("phone_number", unique=True)
        print("✅ Created or ensured unique index on users.number")
    except Exception as e:
        print("⚠️ Failed to create users.number unique index:", e)

    try:
        await db.queries.create_index("phone_number")
        print("✅ Ensured index on queries.phone_number")
    except Exception as e:
        print("⚠️ Failed to create queries.phone_number index:", e)

# -------------------------
# Bootstrap & run
async def _create_indexes(db):
    try:
        await db.users.create_index("phone_number", unique=True)
    except Exception:
        pass
    try:
        await db.queries.create_index("phone_number")
    except Exception:
        pass

def ensure_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


async def build_application_async():
    """
    Build Telegram application for async webhook/serverless use.
    """

    # -----------------------------------------
    # Initialize graph
    # -----------------------------------------
    graph = None

    try:
        if langchain_create_graph:
            graph = langchain_create_graph()
            print("Using langchain_bot.create_graph()")

        elif message_init_graph:
            result = message_init_graph()

            if asyncio.iscoroutine(result):
                graph = await result
            else:
                graph = result

            print("Using message_to_json.init_graph()")

        else:
            print("No graph initializer found.")

    except Exception as e:
        print("Warning: failed to initialize graph:", e)

    # -----------------------------------------
    # MongoDB
    # -----------------------------------------
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB_NAME]

    await _create_indexes(db)

    # -----------------------------------------
    # Telegram Application
    # -----------------------------------------
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.bot_data["db"] = db

    if graph:
        application.bot_data["graph"] = graph

    # -----------------------------------------
    # Register handlers
    # -----------------------------------------
    if build_auth_handler:
        try:
            application.add_handler(build_auth_handler())
            print("Auth ConversationHandler registered.")
        except Exception as e:
            print("Failed to register auth handler:", e)

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        MessageHandler(
            filters.CONTACT,
            contact_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.CONTACT,
            message_handler
        )
    )

    return application


async def create_webhook_application():
    """
    Create and initialize Telegram application for webhook use.
    """

    application = await build_application_async()

    await application.initialize()

    print("✅ Telegram Application initialized for webhook.")

    return application

def main():
    """
    Local development only.
    Runs Telegram bot using polling.
    """

    application = asyncio.run(
        build_application_async()
    )

    application.run_polling(
        poll_interval=1.0,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None,
    )

# Convenience: build a module-level application for webhook use by imports
# def create_webhook_application():
#     """
#     Create and initialize the Telegram application for webhook use.
#     """
#     application = build_application()

#     loop = getattr(application, "bot_loop", None)

#     if loop is None:
#         raise RuntimeError(
#             "Telegram application event loop was not created"
#         )

#     if loop.is_closed():
#         raise RuntimeError(
#             "Telegram application event loop is closed"
#         )

#     loop.run_until_complete(
#         application.initialize()
#     )

#     print("Telegram Application initialized for webhook.")

#     return application


if __name__ == "__main__":
    main()