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

def hash_password(password: str) -> str:
    pre = pre_hash(password)
    hashed = bcrypt.hashpw(pre, bcrypt.gensalt())
    return hashed.decode()

def verify_password(password: str, hashed: str) -> bool:
    pre = pre_hash(password)
    return bcrypt.checkpw(pre, hashed.encode())