import os

from fastapi import FastAPI, Request, HTTPException
from telegram import Update

from bot_runner import create_webhook_application


app = FastAPI()

tg_application = None


@app.on_event("startup")
async def startup():
    global tg_application

    print("🚀 Starting FinMan Telegram webhook...")

    tg_application = await create_webhook_application()

    print("✅ FinMan Telegram webhook ready.")


@app.get("/")
async def health():
    return {
        "status": "ok",
        "message": "FinMan Telegram Bot is running ✅"
    }


@app.post("/webhook")
async def telegram_webhook(request: Request):

    if tg_application is None:
        raise HTTPException(
            status_code=503,
            detail="Telegram application not initialized"
        )

    # Optional Telegram secret-token
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")

    if webhook_secret:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received_secret != webhook_secret:
            print("❌ Invalid Telegram webhook secret")

            raise HTTPException(
                status_code=403,
                detail="Unauthorized"
            )

    # Get Telegram update
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON"
        )

    # Convert JSON to Telegram Update
    try:
        update = Update.de_json(
            data,
            tg_application.bot
        )
    except Exception as e:
        print("❌ Failed to parse update:", repr(e))

        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update"
        )

    # Process update
    try:
        await tg_application.process_update(update)

    except Exception as e:
        print(
            "❌ Error processing Telegram update:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to process update"
        )

    return {"ok": True}