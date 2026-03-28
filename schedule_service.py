"""
schedule_service.py — Time-based match detection from ipl_schedule_export.json.

Rules:
  - Parse MATCH_COMMENCE_START_DATE as IST naive → convert to aware.
  - Current match = latest match whose start_time <= now (IST).
  - Match is "active"  when: start_time <= now <= start_time + 5 hours.
"""

import json
import os
import logging
from datetime import datetime, timedelta

from utils import IST, get_ist_now

logger = logging.getLogger(__name__)

# Path to schedule JSON (same directory as this file)
_SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "ipl_schedule_export.json")
_ACTIVE_WINDOW_HOURS = 5


def _load_schedule() -> list[dict]:
    """Load and return the schedule JSON."""
    with open(_SCHEDULE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_ist(dt_str: str) -> datetime:
    """
    Parse 'YYYY-MM-DD HH:MM:SS' (naive, treated as IST) → aware IST datetime.
    """
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=IST)


def get_current_match() -> dict | None:
    """
    Return the schedule entry for the current match (latest whose
    start_time <= now IST), or None if no match has started yet.
    """
    now = get_ist_now()
    try:
        schedule = _load_schedule()
    except Exception as e:
        logger.error("Failed to load schedule: %s", e)
        return None

    # Collect all matches that have started on or before now
    past_matches = []
    for entry in schedule:
        dt_str = entry.get("MATCH_COMMENCE_START_DATE")
        if not dt_str:
            continue
        try:
            start_time = _parse_ist(dt_str)
        except ValueError as e:
            logger.warning("Bad date format for MatchID %s: %s", entry.get("MatchID"), e)
            continue
        if start_time <= now:
            past_matches.append((start_time, entry))

    if not past_matches:
        return None

    # Latest started match
    past_matches.sort(key=lambda x: x[0])
    start_time, match = past_matches[-1]
    match["_start_time"] = start_time
    match["_end_time"] = start_time + timedelta(hours=_ACTIVE_WINDOW_HOURS)
    return match


def is_match_active(match: dict) -> bool:
    """Return True if `now` falls within the match's active window."""
    now = get_ist_now()
    start = match.get("_start_time")
    end = match.get("_end_time")
    if start is None or end is None:
        return False
    return start <= now <= end
