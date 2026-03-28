"""
worker.py — Background worker that polls live IPL innings data,
            detects changes via hashing, calculates fantasy points,
            and persists everything to MongoDB.

Runs as a standalone process (never crashes).
"""

import time
import logging
import traceback
from datetime import datetime

import requests

import db
import schedule_service
import fantasy_engine
from utils import clean_jsonp, hash_data, get_ist_now

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INNINGS_BASE = (
    "https://ipl-stats-sports-mechanic.s3.ap-south-1.amazonaws.com"
    "/ipl/feeds/{match_id}-{innings}.js"
)

# Number of consecutive unchanged Innings1 polls before switching to Innings2
INNINGS1_STALE_THRESHOLD = 3

POLL_INTERVAL_ACTIVE  = 2    # seconds between polls during match
POLL_INTERVAL_IDLE    = 60   # seconds to sleep when no active match
REQUEST_TIMEOUT       = 10   # seconds


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_innings(match_id: int, innings_label: str) -> tuple[dict | None, str | None]:
    """
    Fetch and parse one innings feed.
    Returns (full_parsed_dict, hash_str) or (None, None) on failure.
    The returned dict is the full wrapper, e.g. {"Innings1": {...}}
    Hash is computed on the inner innings data only to avoid noise.
    """
    url = INNINGS_BASE.format(match_id=match_id, innings=innings_label)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        full_data = clean_jsonp(resp.text)
        if full_data is None:
            logger.warning("Could not parse %s for match %s", innings_label, match_id)
            return None, None
        # Hash only the inner innings dict (the scoring data)
        inner = full_data.get(innings_label, full_data)
        return full_data, hash_data(inner)
    except requests.RequestException as e:
        logger.warning("HTTP error fetching %s (match %s): %s", innings_label, match_id, e)
        return None, None


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def upsert_match(match_id: int, innings1: dict | None, innings2: dict | None,
                 start_time: datetime, end_time: datetime):
    col = db.get_matches_collection()
    col.update_one(
        {"match_id": match_id},
        {"$set": {
            "match_id":   match_id,
            "innings1":   innings1,
            "innings2":   innings2,
            "updated_at": get_ist_now(),
            "start_time": start_time,
            "end_time":   end_time,
        }},
        upsert=True,
    )
    logger.info("Upserted match data for match %s", match_id)


def upsert_points(match_id: int, points_data: list[dict]):
    col = db.get_points_collection()
    col.update_one(
        {"match_id": match_id},
        {"$set": {
            "match_id":   match_id,
            "updated_at": get_ist_now(),
            "data":       points_data,
        }},
        upsert=True,
    )
    logger.info("Upserted fantasy points for match %s (%d players)", match_id, len(points_data))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    logger.info("Worker started.")

    # Per-match state (reset when match_id changes)
    last_match_id:    int | None = None
    last_innings1_hash: str | None = None
    last_innings2_hash: str | None = None
    innings1_stale_count: int = 0
    use_innings2:     bool = False

    # These hold the last good data (to avoid re-fetching on unchanged polls)
    cached_innings1: dict | None = None
    cached_innings2: dict | None = None

    while True:
        try:
            # ----------------------------------------------------------------
            # 1. Detect current match
            # ----------------------------------------------------------------
            match = schedule_service.get_current_match()

            if match is None:
                logger.info("No match has started yet. Sleeping %ds.", POLL_INTERVAL_IDLE)
                time.sleep(POLL_INTERVAL_IDLE)
                continue

            match_id   = match["MatchID"]
            start_time = match["_start_time"]
            end_time   = match["_end_time"]

            # Reset state when match changes
            if match_id != last_match_id:
                logger.info("New match detected: %s (ID %s)", match.get("MatchName"), match_id)
                last_match_id     = match_id
                last_innings1_hash = None
                last_innings2_hash = None
                innings1_stale_count = 0
                use_innings2      = False
                cached_innings1   = None
                cached_innings2   = None

            # ----------------------------------------------------------------
            # 2. Check active window
            # ----------------------------------------------------------------
            if not schedule_service.is_match_active(match):
                logger.info(
                    "Match %s not in active window. Sleeping %ds.",
                    match_id, POLL_INTERVAL_IDLE
                )
                time.sleep(POLL_INTERVAL_IDLE)
                continue

            # ----------------------------------------------------------------
            # 3. Fetch Innings 1
            # ----------------------------------------------------------------
            innings1_data, innings1_hash = fetch_innings(match_id, "Innings1")

            innings1_changed = (innings1_hash is not None and
                                innings1_hash != last_innings1_hash)

            if innings1_changed:
                innings1_stale_count = 0
                last_innings1_hash   = innings1_hash
                cached_innings1      = innings1_data
                logger.info("Innings1 updated (match %s).", match_id)
            elif innings1_hash is not None:
                innings1_stale_count += 1
                logger.debug(
                    "Innings1 unchanged (stale count: %d/%d).",
                    innings1_stale_count, INNINGS1_STALE_THRESHOLD
                )

            # ----------------------------------------------------------------
            # 4. Fetch Innings 2 (after Innings1 turns stale)
            # ----------------------------------------------------------------
            innings2_changed = False

            if use_innings2 or innings1_stale_count >= INNINGS1_STALE_THRESHOLD:
                use_innings2 = True
                innings2_data, innings2_hash = fetch_innings(match_id, "Innings2")

                if innings2_data:
                    innings2_changed = (innings2_hash != last_innings2_hash)
                    if innings2_changed:
                        last_innings2_hash = innings2_hash
                        cached_innings2    = innings2_data
                        logger.info("Innings2 updated (match %s).", match_id)

            # ----------------------------------------------------------------
            # 5. If any data changed → persist + recalculate
            # ----------------------------------------------------------------
            if innings1_changed or innings2_changed:
                # Store raw match data
                upsert_match(
                    match_id,
                    cached_innings1,
                    cached_innings2,
                    start_time,
                    end_time,
                )

                # Calculate and store fantasy points
                try:
                    points = fantasy_engine.calculate_points(cached_innings1, cached_innings2)
                    upsert_points(match_id, points)
                except Exception as e:
                    logger.error("Fantasy engine error (match %s): %s", match_id, e)
                    logger.debug(traceback.format_exc())
            else:
                logger.debug("No data change for match %s. Skipping DB write.", match_id)

        except Exception as e:
            logger.error("Unexpected worker error: %s", e)
            logger.debug(traceback.format_exc())

        time.sleep(POLL_INTERVAL_ACTIVE)


if __name__ == "__main__":
    run()
