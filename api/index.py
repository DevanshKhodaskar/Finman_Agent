# api/index.py
import subprocess
import sys

# Run main.py as a background process
subprocess.Popen([sys.executable, "main.py"])

# Return a simple health endpoint
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "Bot running with polling"}

@app.get("/health")
def health():
    return {"status": "ok"}