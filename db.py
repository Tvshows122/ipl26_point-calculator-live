"""
db.py — MongoDB connection singleton using MONGO_URI env variable.
Database: dream11
Collections: matches, points
"""

import os
import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

logger = logging.getLogger(__name__)

_client: MongoClient = None
_db = None


def get_db():
    """Return the dream11 database instance (lazy singleton)."""
    global _client, _db
    if _db is None:
        mongo_uri = os.environ.get("MONGO_URI")
        if not mongo_uri:
            raise RuntimeError("MONGO_URI environment variable is not set.")
        try:
            _client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            # Verify connection
            _client.admin.command("ping")
            _db = _client["dream11"]
            logger.info("Connected to MongoDB (dream11).")
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", e)
            raise
    return _db


def get_matches_collection():
    return get_db()["matches"]


def get_points_collection():
    return get_db()["points"]
