"""Persistent passage caches. TTL cleanup never substitutes for expiry checks."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pymongo.errors import PyMongoError

from app.core.errors import CacheUnavailable


class SQLiteCache:
    def __init__(self, path):
        self.path = str(path)

    def _connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS passages "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
        except sqlite3.Error:
            db.close()
            raise
        return db

    def get(self, key, now):
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT value FROM passages WHERE key = ? AND expires_at > ?",
                    (key, now.timestamp()),
                ).fetchone()
            return json.loads(row[0]) if row else None
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise CacheUnavailable from exc

    def set(self, key, value, expires_at, now):
        try:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM passages WHERE expires_at <= ?", (now.timestamp(),))
                db.execute(
                    "INSERT OR REPLACE INTO passages VALUES (?, ?, ?)",
                    (key, json.dumps(value), expires_at.timestamp()),
                )
        except (OSError, sqlite3.Error) as exc:
            raise CacheUnavailable from exc

    def initialize(self):
        try:
            with closing(self._connect()):
                pass
        except (OSError, sqlite3.Error) as exc:
            raise CacheUnavailable from exc


class MongoCache:
    def __init__(self, collection):
        self.collection = collection

    def get(self, key, now):
        try:
            row = self.collection.find_one({"_id": key, "expires_at": {"$gt": now}})
            return row["value"] if row else None
        except (PyMongoError, KeyError) as exc:
            raise CacheUnavailable from exc

    def set(self, key, value, expires_at, now):
        try:
            self.collection.replace_one(
                {"_id": key}, {"_id": key, "value": value, "expires_at": expires_at}, upsert=True
            )
        except PyMongoError as exc:
            raise CacheUnavailable from exc

    def initialize(self):
        try:
            self.collection.database.client.admin.command("ping")
            self.collection.create_index("expires_at", expireAfterSeconds=0, name="passage_expiry")
        except PyMongoError as exc:
            raise CacheUnavailable from exc
