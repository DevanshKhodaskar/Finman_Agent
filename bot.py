import asyncio
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram import Update
from langchain_core.messages import HumanMessage
from langchain_bot import create_graph
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

graph = None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    # Run the graph invocation in an executor to avoid blocking
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, 
        lambda: graph.invoke({"messages": [HumanMessage(content=user_input)]})
    )
    ai_message = result["messages"][-1]
    await update.message.reply_text(ai_message.content)

async def main():
    global graph
    
    print("🤖 Bot starting...")
    
    # Initialize the graph
    graph = create_graph()
    print("✅ Graph initialized")
    
    # Build the application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Initialize and start polling
    async with app:
        await app.initialize()
        await app.start()
        print("🤖 Bot is running...")
        await app.updater.start_polling()
        
        # Keep the bot running
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            print("🛑 Bot stopping...")
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("✅ Bot stopped.")