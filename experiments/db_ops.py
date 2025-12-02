# db_ops.py
import re
from datetime import datetime
from typing import Optional, Any, Dict
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

# -------------------------
# Helpers
# -------------------------
def normalize_to_10digits(raw: Optional[Any]) -> Optional[str]:
    """
    Extract only digits and return the last 10 digits (as string).
    Returns None if no digits found.
    """
    if raw is None:
        return None
    s = str(raw)
    digits = re.sub(r"[^\d]", "", s)
    if digits == "":
        return None
    return digits[-10:] if len(digits) >= 10 else digits

def ensure_datetime(value: Optional[Any]) -> datetime:
    """
    Accepts None, datetime, or ISO-like string. Returns datetime.
    If parsing fails or value is None, returns datetime.utcnow().
    """
    if value is None:
        return datetime.utcnow()
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        try:
            return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return datetime.utcnow()

def ensure_price_numeric(value: Any):
    """
    Convert price to numeric (int if integer, else float). Raises ValueError on bad input.
    """
    if value is None:
        raise ValueError("price is required")
    if isinstance(value, (int, float)):
        if isinstance(value, int):
            return value
        return int(value) if float(value).is_integer() else float(value)
    s = str(value)
    cleaned = re.sub(r"[^\d.]", "", s)
    if cleaned == "":
        raise ValueError(f"price is not numeric: {value}")
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    try:
        f = float(cleaned)
        return int(f) if f.is_integer() else f
    except Exception:
        raise ValueError(f"price is not numeric: {value}")

# -------------------------
# Main DB functions
# -------------------------
# inside db_ops.py (replace the existing add_user)
from datetime import datetime

async def add_user(
    db: AsyncIOMotorDatabase,
    name: str,
    number: str,
    telegram_username: Optional[str] = None,
    telegram_id: Optional[int] = None,
) -> Dict[str, Any]:
    norm = normalize_to_10digits(number)
    if not norm or len(norm) != 10:
        raise ValueError("number must normalize to exactly 10 digits (no country code).")

    set_doc = {
        "name": name,
        "number": norm,
        "telegram_username": telegram_username,
        "telegram_id": int(telegram_id) if telegram_id is not None else None
    }

    res = await db.Users.update_one(
        {"number": norm},
        {
            "$set": set_doc,
            "$setOnInsert": {"created_at": datetime.utcnow()},
            "$currentDate": {"updated_at": True}
        },
        upsert=True
    )
    return {
        "matched_count": res.matched_count,
        "modified_count": res.modified_count,
        "upserted_id": str(res.upserted_id) if res.upserted_id else None
    }


async def add_query_for_user(
    db: AsyncIOMotorDatabase,
    number: str,
    name: str,
    category: str,
    price: Any,
    time: Optional[Any] = None,
    extra: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Insert a query for the user identified by `number` (10-digit, no country code).
    Raises LookupError if the user does not exist.
    Returns inserted id.
    """
    norm = normalize_to_10digits(number)
    if not norm or len(norm) != 10:
        raise ValueError("number must normalize to exactly 10 digits (no country code).")
    user = await db.Users.find_one({"number": norm}, {"telegram_id": 1})
    if not user:
        raise LookupError(f"No user found with number {norm}. Insert user first.")
    price_num = ensure_price_numeric(price)
    time_dt = ensure_datetime(time)
    doc = {
        "phone": norm,
        "name": name,
        "category": category,
        "price": price_num,
        "time": time_dt,
        "telegram_id": user.get("telegram_id"),
        "extra": extra or {},
        "created_at": datetime.utcnow()
    }
    res = await db.queries.insert_one(doc)
    return {"inserted_id": str(res.inserted_id)}

async def upsert_user_and_add_query(
    db: AsyncIOMotorDatabase,
    user_obj: Dict[str, Any],
    query_obj: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Upsert the user, then insert the query for that user.
    Returns dictionary with both results.
    """
    upsert_res = await add_user(
        db,
        name=user_obj.get("name"),
        number=user_obj.get("number"),
        telegram_username=user_obj.get("telegram_username"),
        telegram_id=user_obj.get("telegram_id")
    )
    norm = normalize_to_10digits(user_obj.get("number"))
    query_res = await add_query_for_user(
        db,
        number=norm,
        name=query_obj.get("name"),
        category=query_obj.get("category"),
        price=query_obj.get("price"),
        time=query_obj.get("time"),
        extra=query_obj.get("extra")
    )
    return {"user_upsert": upsert_res, "query_insert": query_res}

# -------------------------
# Safe index creation
# -------------------------
async def create_recommended_indexes_safe(db: AsyncIOMotorDatabase):
    """
    Ensure:
    - Users.number unique index exists (name: number_unique)
    - Users.telegram_id unique sparse index exists (name: telegram_id_unique)
    - queries index on (phone:1, time:-1) exists (name: phone_time_idx)
    Safe checks existing indexes and only creates/drops if necessary.
    """
    users = db.Users
    queries = db.queries

    # Inspect existing users indexes
    existing_users_indexes = await users.index_information()

    # Check for an index on number
    number_index_name = None
    number_index_info = None
    for name, info in existing_users_indexes.items():
        key = info.get("key")
        if isinstance(key, dict):
            key_items = list(key.items())
        else:
            key_items = key
        if key_items == [("number", 1)]:
            number_index_name = name
            number_index_info = info
            break

    # If exists and not unique -> drop and recreate as unique
    if number_index_name:
        if not number_index_info.get("unique", False):
            print(f"Users: index '{number_index_name}' on number exists but is not unique - will be replaced.")
            await users.drop_index(number_index_name)
            await users.create_index([("number", 1)], unique=True, name="number_unique")
        else:
            print(f"Users: unique index on 'number' exists (name: {number_index_name}).")
    else:
        print("Users: creating unique index on 'number' -> number_unique")
        await users.create_index([("number", 1)], unique=True, name="number_unique")

    # telegram_id unique sparse
    existing_users_indexes = await users.index_information()
    telegram_ok = False
    for name, info in existing_users_indexes.items():
        key = info.get("key")
        if isinstance(key, dict):
            key_items = list(key.items())
        else:
            key_items = key
        if key_items == [("telegram_id", 1)]:
            if info.get("unique", False):
                telegram_ok = True
                print(f"Users: unique index on 'telegram_id' exists (name: {name}).")
            else:
                print(f"Users: index {name} on telegram_id exists but not unique - replacing.")
                await users.drop_index(name)
            break
    if not telegram_ok:
        print("Users: creating unique sparse index on 'telegram_id' -> telegram_id_unique")
        await users.create_index([("telegram_id", 1)], unique=True, sparse=True, name="telegram_id_unique")

    # queries: phone+time
    existing_queries_indexes = await queries.index_information()
    phone_time_exists = False
    for name, info in existing_queries_indexes.items():
        key = info.get("key")
        if isinstance(key, dict):
            key_items = list(key.items())
        else:
            key_items = key
        if key_items == [("phone", 1), ("time", -1)]:
            phone_time_exists = True
            print(f"queries: index on (phone, time) exists (name: {name}).")
            break

    if not phone_time_exists:
        print("queries: creating index phone_time_idx on (phone:1, time:-1)")
        await queries.create_index([("phone", 1), ("time", -1)], name="phone_time_idx")

    print("Index checks/creation complete.")
