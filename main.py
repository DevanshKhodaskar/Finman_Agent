import os
import asyncio
from flask import Flask, request, abort

from bot_runner import application as tg_application
from telegram import Update

app = Flask(__name__)


@app.get("/")
def health():
    return "Bot is running ✅"


# Webhook endpoint to receive Telegram updates
# Optional secret path segment: if WEBHOOK_SECRET is set it must match the URL segment.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or os.environ.get("TELEGRAM_BOT_TOKEN")


@app.post("/webhook/<secret>")
def webhook(secret: str):
    if not WEBHOOK_SECRET:
        abort(403)
    if secret != WEBHOOK_SECRET and secret != os.environ.get("TELEGRAM_BOT_TOKEN"):
        abort(403)

    data = request.get_json(force=True)
    if not data:
        return "", 400

    try:
        update = Update.de_json(data, tg_application.bot)
    except Exception as e:
        print("Failed to parse Update:", e)
        return "", 400

    # Process update using the application's processing pipeline
    try:
        # Reuse the event loop that initialized the Telegram Application to avoid
        # "attached to different loop" errors. Fall back to the current loop.
        loop = getattr(tg_application, "bot_loop", None) or asyncio.get_event_loop()
        coro = tg_application.process_update(update)
        loop.run_until_complete(coro)
    except Exception as e:
        print("Error while processing update:", e)
        return "", 500

    return "", 200


if __name__ == "__main__":
    # Local dev: run polling bot in background then start Flask server
    import threading
    import bot_runner

    def run_polling():
        bot_runner.main()

    t = threading.Thread(target=run_polling, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
