# bot_final_categorization.py
import asyncio
import json
import os
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from langchain_core.messages import HumanMessage

from langchain_bot import create_graph

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not found in .env")

# Global compiled graph
graph = None

def build_categorization_prompt(user_msg: str) -> str:
    """
    Strict prompt: return EXACTLY one JSON object ONLY with keys:
    Name, category, price.
    category must be exactly one of: Food, Entertainment, Travel, Others
    price must be a string (preserve currency suffix if present).
    """
    return (
        "Extract the item information from the user's message and return EXACTLY one valid JSON object ONLY.\n\n"
        "The JSON must have exactly these keys (case-sensitive):\n"
        '- Name: string or null\n'
        '- category: string or null (choose exactly one from: Food, Entertainment, Travel, Others)\n'
        '- price: string or null (preserve currency suffix if present, e.g. \"10rs\")\n\n'
        "Important rules (MODEL MUST FOLLOW):\n"
        "1) Respond with ONLY the JSON object and nothing else (no explanation, no code fences).\n"
        "2) category must be exactly one of: \"Food\", \"Entertainment\", \"Travel\", \"Others\".\n"
        "3) If you cannot infer a category, set category to \"Others\".\n"
        "4) If any field is missing set it to null.\n\n"
        "Examples (MODEL MUST NOT INCLUDE EXTRA TEXT):\n"
        '{"Name": "Lays", "category": "Food", "price": "10rs"}\n'
        '{"Name": "Movie ticket", "category": "Entertainment", "price": "200rs"}\n'
        '{"Name": "Uber ride", "category": "Travel", "price": "150rs"}\n\n'
        f'User message: "{user_msg}"\n\n'
        "Return ONLY the single JSON object (no extra text)."
    )

def _try_fix_and_load_json(block: str):
    """Try json.loads with small, safe fixes if needed."""
    try:
        return json.loads(block)
    except Exception:
        try:
            fixed = block.strip().replace("'", '"')
            fixed = fixed.replace(",}", "}")
            fixed = fixed.replace(",]", "]")
            return json.loads(fixed)
        except Exception:
            return None

def _normalize_parsed(parsed: dict) -> dict:
    """
    Normalize keys and ensure final output has exact keys:
    Name, category, price (strings or None)
    """
    def get_str_or_null(d, keys):
        for k in keys:
            if k in d and d[k] is not None:
                v = d[k]
                if isinstance(v, (int, float)):
                    v = str(v)
                v = str(v).strip()
                return v if v != "" else None
        return None

    name = get_str_or_null(parsed, ["Name", "name", "item", "Item"])
    category = get_str_or_null(parsed, ["category", "Category", "type"])
    price = get_str_or_null(parsed, ["price", "Price", "amount"])

    # Enforce allowed categories exactly; default to Others if not matched
    allowed = {"Food", "Entertainment", "Travel", "Others"}
    if category is None:
        category = "Others"
    elif category not in allowed:
        # try simple capitalization match
        cat_norm = category.strip().capitalize()
        if cat_norm in allowed:
            category = cat_norm
        else:
            category = "Others"

    return {"Name": name, "category": category, "price": price}

def categorization(user_msg: str) -> dict:
    """
    Main helper that:
    - builds the strict prompt,
    - invokes the compiled graph (graph must be available globally),
    - parses the returned JSON,
    - returns normalized dict {Name, category, price}.
    """
    if graph is None:
        raise RuntimeError("Graph not initialized")

    prompt = build_categorization_prompt(user_msg)
    result = graph.invoke({"messages": [HumanMessage(content=prompt)]})
    model_msg = result["messages"][-1]
    text = getattr(model_msg, "content", str(model_msg))

    # Try direct parse (no regex)
    parsed = _try_fix_and_load_json(text)
    if parsed is None:
        # parsing failed — return a fallback object with raw_text in price field (or handle as None)
        return {"Name": None, "category": "Others", "price": None}

    return _normalize_parsed(parsed)

# Telegram handler uses categorization in executor
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = (update.message.text or "").strip()
    if not user_input:
        print("Empty message received.")
        return

    loop = asyncio.get_event_loop()

    def run_cat():
        try:
            return categorization(user_input)
        except Exception as e:
            return {"error": str(e)}

    result = await loop.run_in_executor(None, run_cat)

    # Print result to terminal ONLY
    print("\n----- NEW MESSAGE -----")
    print("User:", user_input)
    print("Parsed JSON:", json.dumps(result, indent=2, ensure_ascii=False))
    print("-----------------------\n")


async def main():
    global graph

    print("🤖 Initializing graph...")
    graph = create_graph()
    print("✅ Graph initialized")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # start/stop with context manager
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("✅ Bot stopped by user")
    finally:
        loop.close()
