# bot/user_model.py
from datetime import datetime
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

# --------------------------
# USERS collection helpers
# --------------------------

async def find_user_by_phone(db: AsyncIOMotorDatabase, phone10: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by canonical phone. Accepts either 'number' or 'phone_number' for compatibility.
    phone10 should be normalized (10-digit) before calling.
    """
    if not phone10:
        return None
    return await db.Users.find_one({"$or": [{"number": phone10}, {"phone_number": phone10}]})

async def find_user_by_telegram(db: AsyncIOMotorDatabase, tg_id: int) -> Optional[Dict[str, Any]]:
    if tg_id is None:
        return None
    return await db.Users.find_one({"telegram_id": int(tg_id)})


async def create_user(db: AsyncIOMotorDatabase, phone10: str, password_hash: str, name: str = "") -> Dict[str, Any]:
    """
    Atomic upsert — guarantees exactly 1 user per phone.
    """
    if not phone10 or not phone10.isdigit() or len(phone10) != 10:
        raise ValueError("Invalid phone number")

    now = datetime.utcnow()

    filter_q = {"number": phone10}

    update = {
        # INSERT-ONLY fields
        "$setOnInsert": {
            "name": name or "",
            "number": phone10,
            "phone_number": phone10,
            "telegram_username": "",
            "telegram_id": 0,
            "password_hash": password_hash,
            "created_at": now
        },
        # ALWAYS update this on every upsert
        "$set": {
            "updated_at": now
        }
    }

    try:
        doc = await db.Users.find_one_and_update(
            filter_q,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return doc

    except DuplicateKeyError:
        return await db.Users.find_one(filter_q)


async def update_telegram_mapping(db: AsyncIOMotorDatabase, phone10: str, tg_id: int, tg_username: Optional[str]):
    """
    Link Telegram: keep canonical phone fields in sync.
    """
    if not phone10:
        raise ValueError("phone required for update_telegram_mapping")

    await db.Users.update_one(
        {"number": phone10},
        {
            "$set": {
                "telegram_id": int(tg_id) if tg_id is not None else 0,
                "telegram_username": tg_username or "",
                "phone_number": phone10,
                "updated_at": datetime.utcnow(),
            }
        },
    )


async def update_password_hash(db: AsyncIOMotorDatabase, phone10: str, new_hash: str):
    if not phone10:
        raise ValueError("phone required for update_password_hash")
    await db.Users.update_one(
        {"number": phone10},
        {"$set": {"password_hash": new_hash, "updated_at": datetime.utcnow()}}
    )


# --------------------------
# QUERIES collection helpers
# --------------------------

async def create_query(
    db: AsyncIOMotorDatabase,
    phone_number: str,
    price: float,
    name: str,
    category: str,
    isIncome: bool,
    telegram_id: int
) -> str:
    """
    Insert a query using the required schema.
    Returns the inserted document id as string.
    """
    doc = {
        "phone_number": phone_number,
        "price": float(price),
        "name": name or "",
        "category": category or "uncategorized",
        "isIncome": bool(isIncome),
        "time": datetime.utcnow(),
        "telegram_id": str(telegram_id) if telegram_id is not None else None,
        "created_at": datetime.utcnow(),
    }
    res = await db.Queries.insert_one(doc)
    return str(res.inserted_id)
