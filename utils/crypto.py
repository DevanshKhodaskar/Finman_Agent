# utils/crypto.py
import bcrypt
import asyncio

async def hash_password(password: str) -> str:
    loop = asyncio.get_event_loop()
    hashed = await loop.run_in_executor(None, bcrypt.hashpw, password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8')

async def verify_password(password: str, hashed: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, bcrypt.checkpw, password.encode('utf-8'), hashed.encode('utf-8'))
