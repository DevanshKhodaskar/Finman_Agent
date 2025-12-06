# bot/sessions.py
from typing import Dict, Any
import time

# structure: { telegram_id: {"phone":..., "authed": bool, "expires_at": timestamp, "state": {...}}}
_sessions: Dict[str, Dict[str, Any]] = {}

SESSION_TTL_SECONDS = 60 * 60 * 24  # 24 hours default

def create_session(telegram_id: int, phone: str = None, authed: bool = False):
    _sessions[str(telegram_id)] = {
        "phone": phone,
        "authed": authed,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_TTL_SECONDS,
        "state": {}
    }
    return _sessions[str(telegram_id)]

def get_session(telegram_id: int):
    s = _sessions.get(str(telegram_id))
    if not s: return None
    if time.time() > s["expires_at"]:
        _sessions.pop(str(telegram_id), None)
        return None
    return s

def set_session_state(telegram_id: int, key: str, value):
    s = get_session(telegram_id)
    if not s: return None
    s["state"][key] = value
    return s

def destroy_session(telegram_id: int):
    return _sessions.pop(str(telegram_id), None)
