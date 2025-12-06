# utils/crypto.py
import bcrypt
import asyncio

async def generate_hash_and_salt(password: str):
    """
    Generates a bcrypt salt and bcrypt hash.
    This salt can be reused in Node to generate the same hash.
    """
    loop = asyncio.get_event_loop()
    salt = await loop.run_in_executor(None, bcrypt.gensalt)       # random salt
    hashed = await loop.run_in_executor(                           # hash using the same salt
        None, bcrypt.hashpw, password.encode("utf-8"), salt
    )
    return {
        "salt": salt.decode("utf-8"),      # SEND THIS to backend
        "hash": hashed.decode("utf-8")     # Stored password_hash
    }

async def verify_password(password: str, hashed: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        bcrypt.checkpw,
        password.encode("utf-8"),
        hashed.encode("utf-8")
    )
