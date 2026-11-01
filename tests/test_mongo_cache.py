from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from pymongo.errors import ConnectionFailure

from app.repositories.bible_cache import MongoCache
from app.core.errors import CacheUnavailable


def test_mongo_checks_expiry_and_writes_ttl():
    collection = Mock()
    now = datetime.now(timezone.utc)
    collection.find_one.return_value = None
    cache = MongoCache(collection)
    assert cache.get("key", now) is None
    collection.find_one.assert_called_once_with({"_id": "key", "expires_at": {"$gt": now}})
    value = {"text": "texto de teste"}
    expiry = now + timedelta(hours=24)
    cache.set("key", value, expiry, now)
    collection.replace_one.assert_called_once_with(
        {"_id": "key"}, {"_id": "key", "value": value, "expires_at": expiry}, upsert=True)
    cache.initialize()
    collection.database.client.admin.command.assert_called_once_with("ping")
    collection.create_index.assert_called_once_with("expires_at", expireAfterSeconds=0, name="passage_expiry")


@pytest.mark.parametrize("method", ["get", "set", "initialize"])
def test_mongo_outage_is_not_silently_replaced(method):
    collection = Mock()
    collection.find_one.side_effect = ConnectionFailure()
    collection.replace_one.side_effect = ConnectionFailure()
    collection.database.client.admin.command.side_effect = ConnectionFailure()
    cache = MongoCache(collection)
    now = datetime.now(timezone.utc)
    args = {"get": ("key", now), "set": ("key", {}, now, now), "initialize": ()}
    with pytest.raises(CacheUnavailable):
        getattr(cache, method)(*args[method])
