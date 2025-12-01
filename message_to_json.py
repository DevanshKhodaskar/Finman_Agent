# bot_verify_confidence_full.py
import asyncio
import base64
import json
import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any

from telegram import Update, File
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler,
)
from langchain_core.messages import HumanMessage

from langchain_bot import create_graph  # your graph factory that returns a compiled graph

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not found in .env")

# global graph (initialized in main)
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
    user_msg: str, image_b64: Optional[str], force_guess: bool = False
) -> str:
    """
    Strong multimodal prompt. Instruct model to return price as a number (no currency suffix).
    """
    if image_b64:
        img_section = (
            "IMAGE_BASE64_START\n"
            + image_b64
            + "\nIMAGE_BASE64_END\n\n"
            "NOTE: The above is the base64-encoded image. Analyze it carefully."
        )
    else:
        img_section = "No image provided."

    guess_note = (
        "If you are not certain about a value, still MAKE A BEST-EFFORT GUESS and set confidence accordingly."
        if force_guess
        else "If you are not certain about a value, you may set it to null and provide a low confidence."
    )

    return (
        "You are a multimodal assistant with vision. Use the IMAGE (if provided) and the TEXT (if provided) to IDENTIFY "
        "the product Name, category and price. Return EXACTLY one JSON object ONLY (no commentary, no code fences).\n\n"
        "The JSON object must include these fields:\n"
        "- Name: string or null\n"
        "- name_confidence: float between 0.0 and 1.0\n"
        "- category: one of exactly (Food, Entertainment, Travel, Others) or null\n"
        "- category_confidence: float between 0.0 and 1.0\n"
        "- price: number (integer or float) or null — DO NOT include currency symbols or text (e.g. 20, 3.5),\n"
        "         if user supplied '20rs' or '₹20' interpret as 20\n"
        "- price_confidence: float between 0.0 and 1.0\n\n"
        "RULES (MUST FOLLOW):\n"
        "1) Respond ONLY with a single JSON object and nothing else.\n"
        "2) price MUST be a numeric value (no currency suffix). If price cannot be determined, set price to null.\n"
        "3) Confidence must be a number in [0.0, 1.0]. If unsure, set the field to null and a low confidence (e.g. 0.0-0.4).\n"
        "4) If asked to GUESS (see below), provide your best guess and a confidence value reflecting your certainty.\n\n"
        f"{img_section}\n\n"
        f'User text: "{user_msg}"\n\n'
        f"{guess_note}\n\n"
        "Example (model must not include extra text):\n"
        '{"Name":"Lays","name_confidence":0.95,"category":"Food","category_confidence":0.9,"price":20,"price_confidence":0.98}\n'
    )




def _try_fix_and_load_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Try json.loads; if it fails, do minimal safe fixes (single->double quotes, strip trailing commas).
    """
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
    """
    Ensure returned dict has all required keys and convert confidences to float.
    Provide safe defaults (null + 0.0).
    """
    out = {
        "Name": parsed.get("Name") if parsed.get("Name") is not None else None,
        "name_confidence": float(parsed.get("name_confidence") or 0.0),
        "category": parsed.get("category") if parsed.get("category") is not None else None,
        "category_confidence": float(parsed.get("category_confidence") or 0.0),
        "price": parsed.get("price") if parsed.get("price") is not None else None,
        "price_confidence": float(parsed.get("price_confidence") or 0.0),
        "raw_model": parsed,
    }

    # enforce allowed category when present; otherwise keep None
    cat = out["category"]
    if cat is not None:
        cat_norm = str(cat).strip().capitalize()
        if cat_norm in ALLOWED_CATEGORIES:
            out["category"] = cat_norm
        else:
            out["category"] = "Others"

    return out


def categorization_with_confidence(user_msg: str, image_b64: Optional[str]) -> Dict[str, Any]:
    """
    Invoke the graph then, if necessary, re-invoke with force_guess=True to get a best-effort answer.
    Returns normalized dict with confidences and raw_model for debugging.
    """
    global graph
    if graph is None:
        init_graph()   # 👈 instead of raising

    # 1) Normal prompt (no forced guess)
    prompt = build_categorization_prompt_with_confidence(user_msg, image_b64, force_guess=False)
    result = graph.invoke({"messages": [HumanMessage(content=prompt)]})
    model_msg = result["messages"][-1]
    text = getattr(model_msg, "content", str(model_msg))

    parsed = _try_fix_and_load_json(text)
    if parsed:
        normalized = _normalize_confidence_parsed(parsed)
        # If at least one field has decent confidence, return immediately
        if (
            normalized["name_confidence"] >= CONF_THRESH
            or normalized["category_confidence"] >= CONF_THRESH
            or normalized["price_confidence"] >= CONF_THRESH
        ):
            normalized["raw_model"] = text
            return normalized

    # 2) Otherwise, re-prompt asking the model to FORCE A BEST-EFFORT GUESS
    prompt2 = build_categorization_prompt_with_confidence(user_msg, image_b64, force_guess=True)
    result2 = graph.invoke({"messages": [HumanMessage(content=prompt2)]})
    model_msg2 = result2["messages"][-1]
    text2 = getattr(model_msg2, "content", str(model_msg2))

    parsed2 = _try_fix_and_load_json(text2)
    if parsed2:
        normalized2 = _normalize_confidence_parsed(parsed2)
        normalized2["raw_model"] = text2
        return normalized2

    # 3) Fallback: return very low-confidence empty object with raw_model from the first attempt
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
    """
    Async download to bytearray and base64-encode it.
    """
    ba = await file.download_as_bytearray()
    b = bytes(ba)
    return base64.b64encode(b).decode("ascii")


# ---------------- utility & verification helpers ----------------
def format_pretty_json(d: Dict[str, Any]) -> str:
    # Filter to only Name, category, price for printing
    out = {"Name": d.get("Name"), "category": d.get("category"), "price": d.get("price")}
    return json.dumps(out, ensure_ascii=False, indent=2)


def needs_clarification(parsed: Dict[str, Any], thresh: float = CONF_THRESH):
    """
    Return (True, issues_list) if any field confidence < thresh.
    Issues are strings among 'name', 'category', 'price'.
    """
    issues = []
    if parsed.get("name_confidence", 0.0) < thresh:
        issues.append("name")
    if parsed.get("category_confidence", 0.0) < thresh:
        issues.append("category")
    if parsed.get("price_confidence", 0.0) < thresh:
        issues.append("price")
    return (len(issues) > 0, issues)


def _start_verif_question_issues(issues: list) -> str:
    """
    Build a single clarifying question targeted to issues.
    """
    if not issues:
        return "Is this correct? Reply 'yes' to confirm or 'no' to correct."
    # Tailor a concise clarifying question:
    if issues == ["price"]:
        return "I couldn't confidently determine the price. What's the price (e.g., '10rs')?"
    if issues == ["name"]:
        return "I couldn't confidently determine the product name. What is the product called?"
    if issues == ["category"]:
        return "I couldn't confidently determine the category. Which of Food / Entertainment / Travel / Others is it?"
    # multiple issues
    return "I'm unsure about: " + ", ".join(issues) + ". Could you provide those values? (You can reply like: Name, category, price)"


# ---------------- Telegram handlers & flow ----------------
# context.chat_data structure:
# pending = {
#   "stage": "await_clarify" | "verify_flow",
#   "parsed": parsed_dict_from_llm,
#   "image_b64": image_b64_or_None,
#   "user_text": original_user_text,
#   "issues": [...],
# }

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    pending = context.chat_data.get("pending")

    # If awaiting clarification (user previously asked to clarify)
    if pending and pending.get("stage") == "await_clarify":
        # Combine pending context with this clarifying reply and re-run the model
        image_b64 = pending.get("image_b64")
        combined_text = (pending.get("user_text") or "") + " " + user_text
        loop = asyncio.get_event_loop()
        parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence(combined_text, image_b64))
        # Decide again
        ask, issues = needs_clarification(parsed)
        if not ask:
            # Accept and print + reply
            pretty = format_pretty_json(parsed)
            print("\n----- FINALIZED (after clarification) -----")
            print(pretty)
            print("-------------------------------------------\n")
            await update.message.reply_text("Confirmed. Here is the JSON:\n" + pretty)
            context.chat_data.pop("pending", None)
            return
        # still unclear -> ask specific question(s)
        pending.update({"stage": "await_clarify", "parsed": parsed, "issues": issues, "user_text": combined_text})
        context.chat_data["pending"] = pending
        await update.message.reply_text(_start_verif_question_issues(issues))
        return

    # If in verification flow (user previously asked to correct fields)
    if pending and pending.get("stage") == "verify_flow":
        stage = pending.get("substage", "await_verify_response")
        if stage == "await_verify_response":
            # user answered yes/no to "Is this correct?"
            if user_text.lower() in ("yes", "y", "yeah", "correct"):
                parsed = pending.get("parsed")
                pretty = format_pretty_json(parsed)
                print("\n----- CONFIRMED BY USER -----")
                print(pretty)
                print("-----------------------------\n")
                await update.message.reply_text("Confirmed. Here is the JSON:\n" + pretty)
                context.chat_data.pop("pending", None)
                return
            else:
                # user said no -> ask which field to correct
                pending["substage"] = "choose_field"
                context.chat_data["pending"] = pending
                await update.message.reply_text("Which field would you like to correct? Reply with: name / category / price / all")
                return

        if stage == "choose_field":
            choice = user_text.strip().lower()
            if choice not in ("name", "category", "price", "all"):
                await update.message.reply_text("Please reply with one of: name / category / price / all")
                return
            pending["substage"] = "await_correction"
            pending["choice"] = choice
            context.chat_data["pending"] = pending
            await update.message.reply_text(f"Please send the corrected value for '{choice}'.")
            return

        if stage == "await_correction":
            choice = pending.get("choice")
            parsed = pending.get("parsed", {})
            # Apply correction locally
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
                    await update.message.reply_text("Couldn't parse that full JSON. Please send corrected Name, category and price as a JSON or correct fields one by one.")
                    return

            # After correction, go back to verify prompt
            pending["parsed"] = parsed
            pending["substage"] = "await_verify_response"
            context.chat_data["pending"] = pending
            await update.message.reply_text("Got it. " + ("Confirm this:\n" + format_pretty_json(parsed)))
            return

    # No pending flows -> new message triggers categorization (text-only)
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence(user_text, None))

    ask, issues = needs_clarification(parsed)
    if not ask:
        # accept automatically
        pretty = format_pretty_json(parsed)
        print("\n----- NEW MESSAGE (TEXT) ACCEPTED -----")
        print("User text:", user_text)
        print(pretty)
        print("----------------------------------------\n")
        await update.message.reply_text(pretty)
        return

    # low confidence -> start clarification flow (await user clarification)
    context.chat_data["pending"] = {
        "stage": "await_clarify",
        "parsed": parsed,
        "image_b64": None,
        "user_text": user_text,
        "issues": issues,
    }
    await update.message.reply_text(_start_verif_question_issues(issues))


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo
    if not photo:
        return
    file_id = photo[-1].file_id
    file_obj = await context.bot.get_file(file_id)

    try:
        image_b64 = await download_image_base64(file_obj)
    except Exception as e:
        await update.message.reply_text(f"Failed to download image: {e}")
        return

    # initial categorization using image only
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, lambda: categorization_with_confidence("", image_b64))

    ask, issues = needs_clarification(parsed)
    if not ask:
        # accept automatically
        pretty = format_pretty_json(parsed)
        print("\n----- NEW MESSAGE (IMAGE) ACCEPTED -----")
        print(pretty)
        print("-----------------------------------------\n")
        await update.message.reply_text(pretty)
        return

    # low confidence -> ask clarifying question and save pending
    context.chat_data["pending"] = {
        "stage": "await_clarify",
        "parsed": parsed,
        "image_b64": image_b64,
        "user_text": "",
        "issues": issues,
    }
    await update.message.reply_text(_start_verif_question_issues(issues))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot ready. Send text or an image. I will guess fields and ask you only when unsure.")


# ---------------- Main ----------------
async def main():
    global graph
    print("🤖 Initializing graph...")
    graph = create_graph()  # compiled graph that accepts messages via graph.invoke(...)
    print("✅ Graph initialized")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CommandHandler("start", start_command))

    async with app:
        await app.initialize()
        await app.start()
        print("🤖 Bot is running (polling)...")
        await app.updater.start_polling()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            print("🛑 Shutting down bot...")
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    # ensure a fresh event loop for Python 3.14+
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("✅ Bot stopped by user")
    finally:
        loop.close()
