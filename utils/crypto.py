import bcrypt
import hmac
import hashlib
import os

# Load secret from environment variable
SECRET = os.environ.get("SECRET_KEY")
if not SECRET:
    raise ValueError("SECRET_KEY environment variable not set")
SECRET = SECRET.encode("utf-8")  # convert to bytes

def pre_hash(password: str) -> bytes:
    return hmac.new(SECRET, password.encode(), hashlib.sha256).digest()

async def hash_password(password: str) -> str:
    loop = asyncio.get_event_loop()
    hashed = await loop.run_in_executor(None, bcrypt.hashpw, password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8')

async def verify_password(password: str, hashed: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, bcrypt.checkpw, password.encode('utf-8'), hashed.encode('utf-8'))
