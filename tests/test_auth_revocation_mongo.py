"""Opt-in regression against a disposable local replica set, never the configured app database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.auth_repository import MongoAuthRepository


@pytest.fixture
def revocation_database():
    uri = os.getenv('TEST_MONGODB_URI')
    if not uri:
        pytest.skip('TEST_MONGODB_URI is required for isolated local MongoDB tests.')
    if urlsplit(uri).hostname not in ('localhost', '127.0.0.1', '::1'):
        pytest.fail('The disposable MongoDB test requires a loopback address.')
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                         socketTimeoutMS=10000)
    name = 'app_cristao_security_test_' + uuid4().hex
    try:
        client.admin.command('ping')
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def test_old_role_change_cleanup_preserves_a_newer_session(revocation_database):
    database = revocation_database
    account = ObjectId()
    database.users.insert_one({'_id': account, 'role': 'user', 'auth_version': 0})
    database.sessions.insert_one({'user_id': account, 'token_hash': 'legacy'})
    first, second = MongoAuthRepository(database), MongoAuthRepository(database)
    cleanup_started, resume_cleanup = Event(), Event()
    sessions = database.sessions

    class PausedCleanup:
        def delete_many(self, selector):
            cleanup_started.set()
            if not resume_cleanup.wait(timeout=10):
                raise AssertionError('The concurrent role change did not complete.')
            return sessions.delete_many(selector)

    first.sessions = PausedCleanup()
    with ThreadPoolExecutor(max_workers=1) as pool:
        previous_change = pool.submit(first.set_role, account, 'admin')
        try:
            assert cleanup_started.wait(timeout=10)
            assert second.set_role(account, 'user') is True
            database.sessions.insert_one({'user_id': account, 'auth_version': 2,
                                          'token_hash': 'newer-session'})
        finally:
            resume_cleanup.set()
        assert previous_change.result(timeout=10) is True

    assert database.users.find_one({'_id': account})['auth_version'] == 2
    assert database.sessions.find_one({'token_hash': 'newer-session'}) is not None
    assert database.sessions.find_one({'token_hash': 'legacy'}) is None
