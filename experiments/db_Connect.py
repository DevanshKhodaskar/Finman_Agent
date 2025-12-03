import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")

async def test_connection():
    try:
        client = AsyncIOMotorClient(MONGO_URI)
        db = client[MONGO_DB_NAME]

        # MongoDB ping command
        result = await db.command("ping")
        print("MongoDB Connected Successfully! 🎉")
        print(result)

    except Exception as e:
        print("❌ MongoDB Connection Error:", e)


asyncio.run(test_connection())
