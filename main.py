import asyncio
import os

from bot_runner import build_application_async


async def run_local_bot():
    """
    Run FinMan locally using Telegram long polling.

    No webhook, ngrok, or public URL is required.
    """

    print("🚀 Starting FinMan in LOCAL TESTING mode...")

    # Build the Telegram application
    application = await build_application_async()

    # Initialize Telegram application
    await application.initialize()

    print("✅ Telegram application initialized.")

    # Remove any existing Telegram webhook.
    # This is important because polling and webhook mode cannot
    # both be used at the same time.
    try:
        await application.bot.delete_webhook(
            drop_pending_updates=False
        )
        print("✅ Existing webhook removed.")
    except Exception as e:
        print("⚠️ Could not remove webhook:", e)

    # Start Telegram application
    await application.start()

    # Start long polling
    await application.updater.start_polling(
        allowed_updates=None,
        drop_pending_updates=False
    )

    print("✅ FinMan bot is running locally.")
    print("📱 Open Telegram and send /start to your bot.")
    print("Press CTRL+C to stop.")

    try:
        # Keep the process alive
        await asyncio.Event().wait()

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    finally:
        print("\n🛑 Stopping FinMan bot...")

        await application.updater.stop()
        await application.stop()
        await application.shutdown()

        print("✅ Bot stopped.")


def main():
    asyncio.run(run_local_bot())


if __name__ == "__main__":
    main()