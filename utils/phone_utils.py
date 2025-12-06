# utils/phone_utils.py
import re

def normalize_phone(raw: str) -> str:
    """
    Normalize phone to a canonical 10-digit string.
    Returns empty string if normalization fails.
    """
    if not raw:
        return ""

    s = str(raw).strip()
    # remove anything not digit
    s = re.sub(r'\D', '', s)

    # If it has country code 91 at front with length 12, strip leading 91
    if s.startswith("91") and len(s) >= 12:
        s = s[-10:]

    # If leading zero and length 11, strip leading 0
    if s.startswith("0") and len(s) >= 11:
        s = s[-10:]

    # If longer than 10, take last 10 digits (best-effort)
    if len(s) > 10:
        s = s[-10:]

    # final check: must be exactly 10 digits
    if len(s) == 10 and s.isdigit():
        return s

    return ""
