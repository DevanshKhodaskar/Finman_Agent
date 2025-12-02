# db_test.py
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Import helper functions from db_ops.py (same folder)
from db_ops import (
    create_recommended_indexes_safe,
    add_user,
    add_query_for_user,
    upsert_user_and_add_query
)

async def main():
    load_dotenv()  # load .env if present

    MONGO_URI = os.environ.get("MONGO_URI")
    if not MONGO_URI:
        print("ERROR: MONGO_URI environment variable not set.")
        print("Set MONGO_URI in your environment or create a .env file with MONGO_URI=\"your_uri\"")
        return

    client = AsyncIOMotorClient(MONGO_URI)
    db = client["Finman"]
    print("Connected to MongoDB...")

    # Ensure indexes (safe)
    try:
        await create_recommended_indexes_safe(db)
    except Exception as e:
        print("Warning: issue while ensuring indexes:", e)

    # Example 1: upsert a user
    try:
        print("\n-- Upsert user example --")
        res = await add_user(
            db,
            name="Devansh Khodaskar",
            number="+91-9876543210",    # will be normalized to '9876543210'
            telegram_username="devansh_k",
            telegram_id=123456789
        )
        print("add_user result:", res)
    except Exception as e:
        print("add_user error:", e)

    # Example 2: add a query for existing user
    try:
        print("\n-- Add query for existing user --")
        res_q = await add_query_for_user(
            db,
            number="9876543210",
            name="Lays Classic",
            category="Food",
            price="20",   # strings allowed; function will parse numeric
            time=None     # will be set to now
        )
        print("add_query_for_user result:", res_q)
    except Exception as e:
        print("add_query_for_user error:", e)

    # Example 3: upsert user and add query combined
    try:
        print("\n-- Upsert user + add query combined --")
        combined = await upsert_user_and_add_query(
            db,
            user_obj={"name": "Alice", "number": "+919812345678", "telegram_username": "alice", "telegram_id": 222333444},
            query_obj={"name": "Pepsi", "category": "Drink", "price": 40}
        )
        print("combined result:", combined)
    except Exception as e:
        print("upsert_user_and_add_query error:", e)

    client.close()
    print("\nDone.")

if __name__ == "__main__":
    asyncio.run(main())
