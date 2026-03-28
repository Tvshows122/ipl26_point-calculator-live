"""
app.py — Flask REST API for the IPL Fantasy backend.

Endpoints:
  GET /                        → health check
  GET /match                   → current match info (schedule-based)
  GET /data[?match_id=X]       → raw innings data from DB
  GET /points[?match_id=X]     → ranked fantasy points from DB
  GET /fetch/<match_id>        → on-demand: fetch, score & store any match

Run via gunicorn:  gunicorn app:app
"""

import logging
import traceback
from datetime import datetime

import requests
from flask import Flask, jsonify, request

import db
import schedule_service
import fantasy_engine
from utils import IST, clean_jsonp, hash_data, get_ist_now

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

# S3 innings feed base URL (same as worker)
_INNINGS_BASE = (
    "https://ipl-stats-sports-mechanic.s3.ap-south-1.amazonaws.com"
    "/ipl/feeds/{match_id}-{innings}.js"
)
_REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt_to_iso(dt: datetime | None) -> str | None:
    """Serialise a datetime (aware or naive) to ISO-8601 string."""
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _json_error(message: str, status: int = 404):
    return jsonify({"error": message}), status


def _fetch_innings(match_id: int, label: str) -> dict | None:
    """Fetch and parse one innings feed. Returns None on failure."""
    url = _INNINGS_BASE.format(match_id=match_id, innings=label)
    try:
        r = requests.get(url, timeout=_REQUEST_TIMEOUT)
        r.raise_for_status()
        data = clean_jsonp(r.text)
        return data
    except Exception as e:
        logger.warning("Could not fetch %s for match %s: %s", label, match_id, e)
        return None


def _resolve_match_id() -> int | None:
    """
    Return match_id from ?match_id= query param, or from the current
    schedule-detected match. Returns None if neither is available.
    """
    raw = request.args.get("match_id")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    match = schedule_service.get_current_match()
    return match["MatchID"] if match else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def health():
    return jsonify({"status": "API running"}), 200


@app.get("/match")
def current_match():
    """Return metadata about the current (latest started) match."""
    match = schedule_service.get_current_match()
    if match is None:
        return _json_error("No match has started yet.")

    return jsonify({
        "match_id":   match["MatchID"],
        "match_name": match.get("MatchName"),
        "teams": {
            "home": match.get("HomeTeamName"),
            "away": match.get("AwayTeamName"),
        },
        "venue":      match.get("GroundName"),
        "city":       match.get("city"),
        "start_time": _dt_to_iso(match.get("_start_time")),
        "end_time":   _dt_to_iso(match.get("_end_time")),
        "is_active":  schedule_service.is_match_active(match),
    }), 200


@app.get("/data")
def match_data():
    """
    Return raw innings data from MongoDB.
    Uses ?match_id=X if provided, otherwise current schedule match.
    """
    match_id = _resolve_match_id()
    if match_id is None:
        return _json_error("No match detected. Pass ?match_id=<id> or wait for schedule.")

    col = db.get_matches_collection()
    doc = col.find_one({"match_id": match_id}, projection={"_id": 0})

    if doc is None:
        return _json_error(
            f"No data in DB for match {match_id}. "
            f"Try GET /fetch/{match_id} first."
        )

    for key in ("updated_at", "start_time", "end_time"):
        if key in doc:
            doc[key] = _dt_to_iso(doc[key])

    return jsonify({
        "match_id":  match_id,
        "updated_at": doc.get("updated_at"),
        "innings1":   doc.get("innings1"),
        "innings2":   doc.get("innings2"),
    }), 200


@app.get("/points")
def match_points():
    """
    Return ranked fantasy points from MongoDB.
    Uses ?match_id=X if provided, otherwise current schedule match.
    """
    match_id = _resolve_match_id()
    if match_id is None:
        return _json_error("No match detected. Pass ?match_id=<id> or wait for schedule.")

    col = db.get_points_collection()
    doc = col.find_one({"match_id": match_id}, projection={"_id": 0})

    if doc is None:
        return _json_error(
            f"No points in DB for match {match_id}. "
            f"Try GET /fetch/{match_id} first."
        )

    if "updated_at" in doc:
        doc["updated_at"] = _dt_to_iso(doc["updated_at"])

    return jsonify({
        "match_id":   doc.get("match_id"),
        "updated_at": doc.get("updated_at"),
        "data":       doc.get("data", []),
    }), 200


@app.get("/fetch/<int:match_id>")
def fetch_match(match_id: int):
    """
    On-demand: fetch Innings1 + Innings2 from S3 for any match_id,
    calculate fantasy points, and upsert into MongoDB.

    Returns the full points JSON immediately.
    """
    logger.info("On-demand fetch triggered for match %s", match_id)

    # --- Fetch innings ---
    innings1 = _fetch_innings(match_id, "Innings1")
    innings2 = _fetch_innings(match_id, "Innings2")

    if innings1 is None and innings2 is None:
        return _json_error(
            f"Could not fetch any innings data for match {match_id}. "
            "Check if the match ID is correct.",
            status=502,
        )

    # --- Calculate points ---
    try:
        points = fantasy_engine.calculate_points(innings1, innings2)
    except Exception as e:
        logger.error("Fantasy engine error for match %s: %s", match_id, e)
        logger.debug(traceback.format_exc())
        return _json_error(f"Error calculating points: {e}", status=500)

    now = get_ist_now()

    # --- Upsert match data ---
    matches_col = db.get_matches_collection()
    matches_col.update_one(
        {"match_id": match_id},
        {"$set": {
            "match_id":   match_id,
            "innings1":   innings1,
            "innings2":   innings2,
            "updated_at": now,
        }},
        upsert=True,
    )

    # --- Upsert points ---
    points_col = db.get_points_collection()
    points_col.update_one(
        {"match_id": match_id},
        {"$set": {
            "match_id":   match_id,
            "updated_at": now,
            "data":       points,
        }},
        upsert=True,
    )

    logger.info("On-demand fetch complete for match %s (%d players)", match_id, len(points))

    return jsonify({
        "match_id":   match_id,
        "updated_at": _dt_to_iso(now),
        "innings1_fetched": innings1 is not None,
        "innings2_fetched": innings2 is not None,
        "players":    len(points),
        "data":       points,
    }), 200


# ---------------------------------------------------------------------------
# Entry point (dev only — production uses gunicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
