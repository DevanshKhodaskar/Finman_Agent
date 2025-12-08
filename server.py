# server.py
import os
import signal
import subprocess
import sys
import time
from fastapi import FastAPI
import threading
import uvicorn

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

BOT_CMD = [sys.executable, "main.py"]  # uses same Python interpreter

def spawn_bot():
    """
    Spawn the polling bot as a child process and stream its stdout/stderr to parent.
    The function restarts the bot if it exits (optional — here we do a simple restart).
    """
    restart_delay = 3
    while True:
        try:
            print("Starting bot subprocess:", " ".join(BOT_CMD))
            proc = subprocess.Popen(
                BOT_CMD,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
            )

            # Stream output
            for line in proc.stdout:
                print("[bot] " + line.rstrip())

            proc.wait()
            exit_code = proc.returncode
            print(f"Bot subprocess exited with code {exit_code}. Restarting in {restart_delay}s...")
        except Exception as e:
            print("Exception while running bot subprocess:", e)
            time.sleep(restart_delay)

        time.sleep(restart_delay)

def start_bot_in_thread():
    t = threading.Thread(target=spawn_bot, daemon=True)
    t.start()

if __name__ == "__main__":
    # Start the bot subprocess in background
    start_bot_in_thread()

    # Start FastAPI to bind to the expected $PORT (Render requires this)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
