# message_to_json.py
import asyncio
import base64
import json
import os
import pprint
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from datetime import datetime

from telegram import Update, File
from telegram.ext import (
    ContextTypes,
)
from langchain_core.messages import HumanMessage

from langchain_bot import create_graph

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    # This module may also be imported from bot.auth_handlers, but ensure env present when run standalone.
    print("WARNING: TELEGRAM_BOT_TOKEN not found in .env (message_to_json.py)")
    # don't raise here to allow unit tests / import in non-bot contexts


# ---------- Graph / LLM utilities ----------
graph = None


def init_graph():
    """
    Initialize the global graph if it's not already initialized.
    Safe to call multiple times.
    """
    global graph
    if graph is not None:
        return
    print("🤖 Initializing graph (from message_to_json.init_graph)...")
    graph = create_graph()
    print("✅ Graph initialized")


# Confidence threshold: accept LLM field if >= this value
CONF_THRESH = 0.70

ALLOWED_CATEGORIES = {"Food", "Entertainment", "Travel", "Others"}

def build_categorization_prompt_with_confidence(
    user_msg: str,
    image_present: bool,
    force_guess: bool = False,
) -> str:
    """
    Prompt that supports two image modes:
      - RECEIPT / BILL / INVOICE: extract vendor name and TOTAL amount (treat whole bill as one expense).
      - PRODUCT PACKAGING / PHOTO: extract product name, price (from caption or image if visible), and category.
    Also supports text-only income detection (salary, credited, received, refund, deposit).
    """

    receipt_rules = (
        "RECEIPT/BILL RULES:\n"
        "- If the image is a bill, receipt, or invoice: extract the FINAL TOTAL amount (search for 'Total', 'Amount', "
        "'Grand Total', 'Payable', etc.). Use the vendor/shop name from the top of the receipt as Name if available.\n"
        "- Treat the whole bill as a single expense: do NOT attempt to return multiple line items.\n"
        "- Set isIncome = false for receipts.\n"
    )

    product_rules = (
        "PRODUCT/PACKAGING RULES:\n"
        "- If the image is a product/package/photo (e.g., chips packet, bottle), try to identify the product name from the packaging.\n"
        "- If the user provided a caption that contains a numeric value (e.g., '20rs', '₹20', '20'), prefer that as price.\n"
        "- If price is visible on the packaging, extract it. Otherwise set price to null and low confidence.\n"
    )

    text_income_rules = (
        "TEXT-ONLY / INCOME RULES:\n"
        "- If the message text contains income keywords: salary, credited, received, deposit, refund -> isIncome = true.\n"
        "- For lines like 'Salary 300000' or 'received 5000', set Name to a short label (e.g., 'Salary' or 'Income'), category = Others, and price = numeric value.\n"
    )

    guess_note = (
        "If uncertain, MAKE YOUR BEST-EFFORT GUESS and set confidences appropriately."
        if force_guess
        else "If uncertain, you may return null for fields and set low confidence (0.0-0.4)."
    )

    return (
        "You are a multimodal assistant that extracts structured financial entries from text and images.\n\n"

        "Return EXACTLY ONE raw JSON object ONLY with these fields:\n"
        "  Name: string or null\n"
        "  name_confidence: number (0.0-1.0)\n"
        "  category: one of (Food, Entertainment, Travel, Others) or null\n"
        "  category_confidence: number (0.0-1.0)\n"
        "  price: number or null (convert '₹20', '20rs', 'Rs 20' to 20)\n"
        "  price_confidence: number (0.0-1.0)\n"
        "  isIncome: boolean\n\n"

        "GLOBAL RULES:\n"
        "1) Output ONLY the JSON object, nothing else (no explanation, no trailing text).\n"
        "2) price must be numeric when present. If only a caption contains the price, prefer caption value.\n"
        "3) If the image is a receipt -> follow RECEIPT/BILL RULES below. If a product/package -> follow PRODUCT/PACKAGING RULES below.\n"
        "4) If text contains income-related keywords use TEXT-ONLY/INCOME RULES.\n"
        "5) Provide meaningful confidence values for each field. High confidence (>=0.7) means you are fairly certain.\n\n"

        f"{receipt_rules}\n"
        f"{product_rules}\n"
        f"{text_income_rules}\n"

        f"IMAGE_PRESENT: {image_present}\n"
        f'USER_TEXT: \"{user_msg}\"\n\n'

        f"{guess_note}\n\n"

        "EXAMPLES (only JSON):\n"
        '{"Name":"Apoorva Delicacies","name_confidence":0.92,"category":"Food","category_confidence":0.88,"price":635,"price_confidence":0.95,"isIncome":false}\n'
        '{"Name":"Lays Classic","name_confidence":0.88,"category":"Food","category_confidence":0.80,"price":20,"price_confidence":0.93,"isIncome":false}\n'
        '{"Name":"Salary","name_confidence":0.95,"category":"Others","category_confidence":0.8,"price":300000,"price_confidence":0.98,"isIncome":true}\n'
    )


def _try_fix_and_load_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        try:
            fixed = text.strip().replace("'", '"')
            fixed = fixed.replace(",}", "}")
            fixed = fixed.replace(",]", "]")
            return json.loads(fixed)
        except Exception:
            return None


def _normalize_confidence_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "Name": parsed.get("Name") if parsed.get("Name") is not None else None,
        "name_confidence": float(parsed.get("name_confidence") or 0.0),
        "category": parsed.get("category") if parsed.get("category") is not None else None,
        "category_confidence": float(parsed.get("category_confidence") or 0.0),
        "price": parsed.get("price") if parsed.get("price") is not None else None,
        "price_confidence": float(parsed.get("price_confidence") or 0.0),
        "raw_model": parsed,
    }

    cat = out["category"]
    if cat is not None:
        cat_norm = str(cat).strip().capitalize()
        if cat_norm in ALLOWED_CATEGORIES:
            out["category"] = cat_norm
        else:
            out["category"] = "Others"

    return out


def _build_human_message_with_optional_image(
    user_msg: str,
    image_b64: Optional[str],
    force_guess: bool,
) -> HumanMessage:
    image_present = image_b64 is not None
    prompt_text = build_categorization_prompt_with_confidence(
        user_msg=user_msg,
        image_present=image_present,
        force_guess=force_guess,
    )

    if image_b64:
        data_url = f"data:image/jpeg;base64,{image_b64}"
        content = [
            {"type": "text", "text": prompt_text},
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                },
            },
        ]
        return HumanMessage(content=content)

    return HumanMessage(content=prompt_text)


def categorization_with_confidence(user_msg: str, image_b64: Optional[str]) -> Dict[str, Any]:
    global graph
    if graph is None:
        init_graph()

    human_msg = _build_human_message_with_optional_image(
        user_msg=user_msg,
        image_b64=image_b64,
        force_guess=False,
    )

    result = graph.invoke({"messages": [human_msg]})
    model_msg = result["messages"][-1]
    text = getattr(model_msg, "content", str(model_msg))

    parsed = _try_fix_and_load_json(text)
    if parsed:
        normalized = _normalize_confidence_parsed(parsed)
        if (
            normalized["name_confidence"] >= CONF_THRESH
            or normalized["category_confidence"] >= CONF_THRESH
            or normalized["price_confidence"] >= CONF_THRESH
        ):
            normalized["raw_model"] = text
            return normalized

    human_msg2 = _build_human_message_with_optional_image(
        user_msg=user_msg,
        image_b64=image_b64,
        force_guess=True,
    )

    result2 = graph.invoke({"messages": [human_msg2]})
    model_msg2 = result2["messages"][-1]
    text2 = getattr(model_msg2, "content", str(model_msg2))

    parsed2 = _try_fix_and_load_json(text2)
    if parsed2:
        normalized2 = _normalize_confidence_parsed(parsed2)
        normalized2["raw_model"] = text2
        return normalized2

    return {
        "Name": None,
        "name_confidence": 0.0,
        "category": None,
        "category_confidence": 0.0,
        "price": None,
        "price_confidence": 0.0,
        "raw_model": text if parsed is None else text2,
    }


# ---------------- image download helper ----------------
async def download_image_base64(file: File) -> str:
    ba = await file.download_as_bytearray()
    b = bytes(ba)
    return base64.b64encode(b).decode("ascii")


# ---------------- utility & verification helpers ----------------
def format_pretty_json(d: Dict[str, Any]) -> str:
    """Format parsed expense data in a user-friendly way."""
    name = d.get("Name") or d.get("name") or "Unknown"
    category = d.get("category") or "Unknown"
    price = d.get("price") or 0
    return f"📝 *{name}*\n📁 Category: {category}\n💰 Amount: ₹{price}"


def needs_clarification(parsed: Dict[str, Any], thresh: float = CONF_THRESH):
    """
    Clarification logic:
    - If the model is confident about PRICE (meaning it successfully found the receipt total),
      we should NOT ask for clarification even if name/category confidence is low.
    - Only when price itself is low confidence should we enter clarification mode.
    """
    price_conf = parsed.get("price_confidence", 0.0)

    # If price is confident → accept immediately (especially for receipts)
    if price_conf >= thresh:
        return (False, [])

    # Otherwise fall back to normal rules
    issues = []
    if parsed.get("name_confidence", 0.0) < thresh:
        issues.append("name")
    if parsed.get("category_confidence", 0.0) < thresh:
        issues.append("category")
    if price_conf < thresh:
        issues.append("price")

    return (len(issues) > 0, issues)



def _start_verif_question_issues(issues: list) -> str:
    if not issues:
        return "✅ *Does this look correct?*\n\nReply *yes* to save or *no* to make changes."
    if issues == ["price"]:
        return "💰 *What's the price?*\n\nI couldn't determine the amount. Please reply with the price (e.g., \"50\" or \"₹150\")."
    if issues == ["name"]:
        return "📝 *What's the item name?*\n\nI couldn't identify the product. Please tell me what you purchased."
    if issues == ["category"]:
        return "📁 *Which category?*\n\nPlease choose: *Food* / *Entertainment* / *Travel* / *Others*"
    return "🤔 *Need a bit more info!*\n\nI'm unsure about: *" + "*, *".join(issues) + "*\n\nPlease provide these details (e.g., \"Coffee, Food, 50\")."


# ---------------- DB helper to store a query using your schema ----------------
async def _store_query_for_user(db, telegram_id: int, parsed: Dict[str, Any], session_phone: Optional[str] = None) -> Optional[str]:
    """
    Insert a document into db.Queries using the schema:
      phone_number: String,
      price: Number,
      name: String,
      category: String,
      isIncome: Boolean,
      time: Date,
      telegram_id: String
    Uses session_phone first if provided, otherwise tries to resolve user by telegram id.
    Returns inserted_id (str) or None on failure.
    """
    try:
        # prefer session phone if caller provides it
        phone = None
        if session_phone:
            phone = session_phone

        # if no phone from session, resolve by telegram mapping
        if not phone:
            tg_str = str(telegram_id)
            # try numeric, then string fields
            user = await db.Users.find_one({"telegram_id": telegram_id})
            if not user:
                user = await db.Users.find_one({"telegram_id": tg_str})
            if not user:
                # last-resort: find any user whose telegram_username exists and matches (low confidence)
                # NOTE: we avoid over-broad queries in prod
                user = await db.Users.find_one({"telegram_username": {"$exists": True}})
            if not user:
                return None
            phone = user.get("phone_number") or user.get("phone_number") or user.get("phone") or user.get("mobile")
            if not phone:
                return None

        # Normalize price to number
        price_raw = parsed.get("price")
        price_num = None
        if price_raw is not None:
            try:
                if isinstance(price_raw, str):
                    tmp = price_raw.strip().lower().replace("₹", "").replace("rs", "").replace(",", "").replace("$", "")
                    price_num = float(tmp) if ("." in tmp) else int(tmp)
                elif isinstance(price_raw, (int, float)):
                    price_num = price_raw
            except Exception:
                price_num = None

        doc = {
            "phone_number": phone,
            "price": price_num if price_num is not None else 0,
            "name": parsed.get("Name") or parsed.get("name") or "",
            "category": parsed.get("category") or "uncategorized",
            "isIncome": bool(parsed.get("isIncome", False)),
            "time": datetime.utcnow(),
            "telegram_id": str(telegram_id),
            "created_at": datetime.utcnow(),
            "raw_model": parsed.get("raw_model") if isinstance(parsed.get("raw_model"), (str, dict)) else parsed.get("raw_model"),
        }

        if db is None:
            print("DB is None in _store_query_for_user; aborting insert.")
            return None

        res = await db.Queries.insert_one(doc)
        return str(res.inserted_id)
    except Exception as e:
        print("DB insert failed in _store_query_for_user:", e)
        return None


# ---------------- New export: parse_message_to_entry ----------------
def parse_message_to_entry(text: str) -> Dict[str, Any]:
    """
    Synchronous helper expected by auth_handlers.add_query_handler.
    It runs the model synchronously (this may block) by calling categorization_with_confidence.
    Returns a simple dict with keys: price (number or 0), name (str), category (str), isIncome (bool).
    """
    global graph
    if graph is None:
        init_graph()

    parsed = categorization_with_confidence(text, None)

    price_raw = parsed.get("price")
    price_num = 0
    if price_raw is not None:
        try:
            if isinstance(price_raw, str):
                tmp = price_raw.strip().lower().replace("₹", "").replace("rs", "").replace(",", "").replace("$", "")
                price_num = float(tmp) if ("." in tmp) else int(tmp)
            elif isinstance(price_raw, (int, float)):
                price_num = price_raw
        except Exception:
            price_num = 0

    name = parsed.get("Name") or parsed.get("name") or ""
    category = parsed.get("category") or "uncategorized"
    isIncome = bool(parsed.get("isIncome", False))

    return {"price": price_num, "name": name, "category": category, "isIncome": isIncome}


# ---------------- Telegram handlers & flow (modified to store to DB) ----------------
# NOTE: these handlers are reusable but your actual bot registers them elsewhere.
# The two main handlers of interest: handle_text and handle_image

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    pending = context.chat_data.get("pending")
    db = context.bot_data.get("db")

    # If awaiting clarification (user previously asked to clarify)
    if pending and pending.get("stage") == "await_clarify":
        image_b64 = pending.get("image_b64")
        combined_text = (pending.get("user_text") or "") + " " + user_text
        loop = asyncio.get_event_loop()
        parsed = await loop.run_in_executor(
            None,
            lambda: categorization_with_confidence(combined_text, image_b64),
        )
        ask, issues = needs_clarification(parsed)
        if not ask:
            inserted = None
            if db is not None:
                # prefer session phone if present
                session_phone = None
                try:
                    session = context.user_data.get("session") or context.chat_data.get("session")
                    # if you use session store elsewhere, adapt this
                except Exception:
                    session = None
                inserted = await _store_query_for_user(db, update.effective_user.id, parsed, session_phone=session_phone)
            pretty = format_pretty_json(parsed)
            if inserted:
                await update.message.reply_text(f"✅ *Saved successfully!*\n\n{pretty}", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"⚠️ *Confirmed but couldn't save.*\n\nThere was an issue saving to the database.\n\n{pretty}", parse_mode="Markdown")
            context.chat_data.pop("pending", None)
            return

        pending.update({
            "stage": "await_clarify",
            "parsed": parsed,
            "issues": issues,
            "user_text": combined_text,
        })
        context.chat_data["pending"] = pending
        await update.message.reply_text(_start_verif_question_issues(issues))
        return

    # If in verification flow (user previously asked to correct fields)
    if pending and pending.get("stage") == "verify_flow":
        stage = pending.get("substage", "await_verify_response")
        if stage == "await_verify_response":
            if user_text.lower() in ("yes", "y", "yeah", "correct"):
                parsed = pending.get("parsed")
                inserted = None
                if db is not None:
                    inserted = await _store_query_for_user(db, update.effective_user.id, parsed)
                pretty = format_pretty_json(parsed)
                if inserted:
                    await update.message.reply_text(f"✅ *Saved successfully!*\n\n{pretty}", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"⚠️ *Confirmed but couldn't save.*\n\nThere was an issue saving to the database.\n\n{pretty}", parse_mode="Markdown")
                context.chat_data.pop("pending", None)
                return
            else:
                pending["substage"] = "choose_field"
                context.chat_data["pending"] = pending
                await update.message.reply_text(
                    "📝 *Which field would you like to correct?*\n\nReply with: `name` / `category` / `price` / `all`",
                    parse_mode="Markdown"
                )
                return

        if stage == "choose_field":
            choice = user_text.strip().lower()
            if choice not in ("name", "category", "price", "all"):
                await update.message.reply_text("🤔 Please reply with one of: `name` / `category` / `price` / `all`", parse_mode="Markdown")
                return
            pending["substage"] = "await_correction"
            pending["choice"] = choice
            context.chat_data["pending"] = pending
            await update.message.reply_text(f"✏️ Please send the corrected value for *{choice}*.", parse_mode="Markdown")
            return

        if stage == "await_correction":
            choice = pending.get("choice")
            parsed = pending.get("parsed", {})
            if choice == "name":
                parsed["Name"] = user_text.strip()
            elif choice == "category":
                cat = user_text.strip().capitalize()
                parsed["category"] = cat if cat in ALLOWED_CATEGORIES else "Others"
            elif choice == "price":
                parsed["price"] = user_text.strip()
            elif choice == "all":
                parsed_try = _try_fix_and_load_json(user_text)
                if parsed_try:
                    parsed = _normalize_confidence_parsed(parsed_try)
                else:
                    await update.message.reply_text(
                        "🤔 *Couldn't understand that format.*\n\nPlease send the corrected values as JSON or correct fields one by one.",
                        parse_mode="Markdown"
                    )
                    return

            pending["parsed"] = parsed
            pending["substage"] = "await_verify_response"
            context.chat_data["pending"] = pending
            await update.message.reply_text(
                f"👍 *Got it!* Please confirm this is correct:\n\n{format_pretty_json(parsed)}\n\nReply *yes* to save or *no* to make changes.",
                parse_mode="Markdown"
            )
            return

    # No pending flows -> new message triggers categorization (text-only)
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(
        None,
        lambda: categorization_with_confidence(user_text, None),
    )

    ask, issues = needs_clarification(parsed)
    if not ask:
        inserted = None
        if db is not None:
            inserted = await _store_query_for_user(db, update.effective_user.id, parsed)
        pretty = format_pretty_json(parsed)
        if inserted:
            await update.message.reply_text(f"✅ *Saved successfully!*\n\n{pretty}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ *Couldn't save this expense.*\n\nThere was an issue with the database.\n\n{pretty}", parse_mode="Markdown")
        return

    context.chat_data["pending"] = {
        "stage": "await_clarify",
        "parsed": parsed,
        "image_b64": None,
        "user_text": user_text,
        "issues": issues,
    }
    await update.message.reply_text(_start_verif_question_issues(issues))


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Process image: download, run LLM graph, store result (if user is linked).
    This function is safe to call from an auth wrapper.
    """
    photo = update.message.photo
    if not photo:
        await update.message.reply_text("📷 *No image found.*\n\nPlease send a photo of your receipt or bill.", parse_mode="Markdown")
        return

    caption = (update.message.caption or "").strip()
    file_id = photo[-1].file_id
    file_obj = await context.bot.get_file(file_id)

    try:
        image_b64 = await download_image_base64(file_obj)
    except Exception as e:
        await update.message.reply_text("📷 *Couldn't process your image.*\n\nPlease try sending it again or use a smaller image.", parse_mode="Markdown")
        return

    # Run LLM in executor (blocking), to avoid blocking event loop
    loop = asyncio.get_event_loop()
    try:
        parsed = await loop.run_in_executor(
            None,
            lambda: categorization_with_confidence(caption, image_b64),
        )
    except Exception as e:
        print("LLM invocation failed:", e)
        await update.message.reply_text("⚠️ *Something went wrong.*\n\nPlease try again in a moment.", parse_mode="Markdown")
        return

    ask, issues = needs_clarification(parsed)
    if not ask:
        db = context.bot_data.get("db")
        inserted = None
        # prefer to use session phone if present (auth_handlers sets session)
        session_phone = None
        try:
            # session is stored in your session manager (not in chat_data by default)
            # If your session store provides a mapping, adapt accordingly.
            # Here we look for a phone in context.user_data or context.chat_data as a best-effort.
            session_phone = context.user_data.get("phone") or context.chat_data.get("phone")
        except Exception:
            session_phone = None

        if db is not None:
            inserted = await _store_query_for_user(db, update.effective_user.id, parsed, session_phone=session_phone)
        pretty = format_pretty_json(parsed)
        if inserted:
            await update.message.reply_text(f"📷 *Expense from image saved!*\n\n{pretty}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ *Couldn't save this expense.*\n\nThere was an issue with the database.\n\n{pretty}", parse_mode="Markdown")
        return

    # If low confidence -> ask clarifying question and save pending
    context.chat_data["pending"] = {
        "stage": "await_clarify",
        "parsed": parsed,
        "image_b64": image_b64,
        "user_text": caption,
        "issues": issues,
    }
    await update.message.reply_text(_start_verif_question_issues(issues))


# NOTE: start_command/main are kept for standalone running/testing of this module.
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Budget Manager is ready!*\n\n"
        "Send me a text message or photo of a receipt, and I'll help you track your expenses.\n\n"
        "💡 *Examples:*\n"
        "• \"Coffee ₹50\"\n"
        "• \"Lunch at restaurant 350\"\n"
        "• Photo of a bill or receipt",
        parse_mode="Markdown"
    )


# The module can be run standalone for offline testing, but usually auth_handlers delegates to handle_image.
if __name__ == "__main__":
    print("This module is normally imported by your bot. Run main.py to start the bot.")
