"""
utils.py — Shared helpers: hash generation, JSONP cleaning, IST timezone.
"""

import hashlib
import re
import json
from datetime import timezone, timedelta

# Indian Standard Time offset
IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now():
    """Return current datetime in IST (timezone-aware)."""
    from datetime import datetime
    return datetime.now(IST)


def clean_jsonp(raw_text: str) -> dict | None:
    """
    Strip wrapping function calls like onScoring({...}) and return parsed dict.
    Returns None if parsing fails.
    """
    if not raw_text:
        return None
    # Remove leading/trailing whitespace
    text = raw_text.strip()
    # Strip any function wrapper: word_chars( ... )
    match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(([\s\S]*)\)\s*;?\s*$", text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def hash_data(data: dict | str) -> str:
    """Return an MD5 hex digest of the serialised data."""
    if isinstance(data, dict):
        serialised = json.dumps(data, sort_keys=True, ensure_ascii=False)
    else:
        serialised = str(data)
    return hashlib.md5(serialised.encode("utf-8")).hexdigest()
