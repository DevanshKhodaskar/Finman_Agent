import threading
import os
from flask import Flask

app = Flask(__name__)

def run_bot():
    # import your old bot file
    import bot_runner  
    bot_runner.main()  # call the function that starts your bot
                       # (if your bot starts automatically, remove .main())

@app.get("/")
def health():
    return "Bot is running ✅"

if __name__ == "__main__":
    # Start bot in background
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    # Render requires this
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
