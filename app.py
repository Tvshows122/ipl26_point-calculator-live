"""
app.py — Flask REST API for the IPL Fantasy backend.

Endpoints:
  GET /           → health check
  GET /match      → current match info
  GET /data       → raw innings data from DB
  GET /points     → ranked fantasy points

Run via gunicorn:  gunicorn app:app
"""

import logging
from datetime import datetime
from flask import Flask, jsonify

import db
import schedule_service
from utils import IST

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")


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
    """Return raw innings data for the current match from MongoDB."""
    match = schedule_service.get_current_match()
    if match is None:
        return _json_error("No match has started yet.")

    match_id = match["MatchID"]
    col = db.get_matches_collection()
    doc = col.find_one({"match_id": match_id}, projection={"_id": 0})

    if doc is None:
        return _json_error(f"No data yet for match {match_id}. Worker may not have run.")

    # Convert datetimes to ISO strings for JSON serialisation
    for key in ("updated_at", "start_time", "end_time"):
        if key in doc:
            doc[key] = _dt_to_iso(doc[key])

    return jsonify({
        "match_id": match_id,
        "updated_at": doc.get("updated_at"),
        "innings1":   doc.get("innings1"),
        "innings2":   doc.get("innings2"),
    }), 200


@app.get("/points")
def match_points():
    """Return ranked fantasy points for the current match."""
    match = schedule_service.get_current_match()
    if match is None:
        return _json_error("No match has started yet.")

    match_id = match["MatchID"]
    col = db.get_points_collection()
    doc = col.find_one({"match_id": match_id}, projection={"_id": 0})

    if doc is None:
        return _json_error(f"No points calculated yet for match {match_id}. Worker may not have run.")

    if "updated_at" in doc:
        doc["updated_at"] = _dt_to_iso(doc["updated_at"])

    return jsonify({
        "match_id":   doc.get("match_id"),
        "updated_at": doc.get("updated_at"),
        "data":       doc.get("data", []),
    }), 200


# ---------------------------------------------------------------------------
# Entry point (dev only — production uses gunicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
