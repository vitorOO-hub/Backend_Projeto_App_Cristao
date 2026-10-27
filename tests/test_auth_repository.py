from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure, ServerSelectionTimeoutError

from app.auth_repository import MongoAuthRepository
from app.errors import AuthUnavailable, EmailConflict, UsernameConflict


@pytest.fixture
def repository():
    database = {"users": MagicMock(), "sessions": MagicMock(), "login_attempts": MagicMock()}
    return MongoAuthRepository(database)


def test_indexes(repository):
    repository.initialize()
    assert repository.users.create_index.call_count == 2
    repository.users.create_index.assert_any_call('email_normalized', unique=True, name='users_email_unique')
    repository.users.create_index.assert_any_call(
        'username_normalized', unique=True, name='users_username_unique',
        partialFilterExpression={'username_normalized': {'$type': 'string'}})
    assert repository.sessions.create_index.call_count == 2
    repository.attempts.create_index.assert_any_call(
        [('key_hash', 1), ('created_at', 1)], name='login_attempts_key')
    repository.attempts.create_index.assert_any_call(
        'expires_at', expireAfterSeconds=0, name='login_attempts_expiry')
    repository.sessions.create_index.assert_any_call('token_hash', unique=True, name='sessions_token_unique')
    repository.sessions.create_index.assert_any_call('expires_at', expireAfterSeconds=0, name='sessions_expiry')


def test_session_queries_and_rotation(repository):
    at = datetime.now(timezone.utc)
    repository.find_session('hashed')
    repository.sessions.find_one.assert_called_once_with({'token_hash': 'hashed'})
    repository.sessions.update_one.return_value.matched_count = 1
    assert repository.rotate_csrf('hashed', 'csrf-hash', at)
    repository.sessions.update_one.assert_called_once_with(
        {'token_hash': 'hashed', 'expires_at': {'$gt': at}}, {'$set': {'csrf_hash': 'csrf-hash'}})
    repository.sessions.update_one.return_value.matched_count = 0
    assert not repository.rotate_csrf('hashed', 'csrf-hash', at)
    document = {'token_hash': 'hashed'}
    repository.insert_session(document)
    repository.sessions.insert_one.assert_called_once_with(document)


def test_atomic_insert_and_duplicate(repository):
    user = {'email_normalized': 'a@example.com'}
    assert repository.insert_user(user) == user
    repository.users.insert_one.assert_called_once_with(user)
    repository.users.insert_one.side_effect = DuplicateKeyError('duplicate')
    with pytest.raises(EmailConflict):
        repository.insert_user(user)
    repository.users.insert_one.side_effect = DuplicateKeyError(
        'duplicate', 11000, {'keyPattern': {'username_normalized': 1}})
    with pytest.raises(UsernameConflict):
        repository.insert_user(user)
    repository.users.find_one.assert_not_called()


def test_username_query(repository):
    repository.find_user_by_username('maria.silva')
    repository.users.find_one.assert_called_once_with({'username_normalized': 'maria.silva'})
    with pytest.raises(ValueError):
        repository.find_user_by_username({'$ne': None})


@pytest.mark.parametrize('method, collection, operation, args', [
    ('initialize', 'users', 'create_index', ()),
    ('find_session', 'sessions', 'find_one', ('hash',)),
    ('insert_session', 'sessions', 'insert_one', ({},)),
    ('rotate_csrf', 'sessions', 'update_one', ('hash', 'csrf', datetime.now(timezone.utc))),
    ('insert_user', 'users', 'insert_one', ({},)),
    ('find_user_by_email', 'users', 'find_one', ('a@example.com',)),
    ('find_user_by_id', 'users', 'find_one', ('id',)),
    ('update_user', 'users', 'find_one_and_update', ('id', {'bio': 'x'})),
    ('delete_session', 'sessions', 'delete_one', ('hash',)),
    ('failed_logins', 'attempts', 'find', ('key', datetime.now(timezone.utc))),
    ('record_failed_login', 'attempts', 'insert_one', ('key', datetime.now(timezone.utc), datetime.now(timezone.utc))),
    ('clear_failed_logins', 'attempts', 'delete_many', ('key',))])
@pytest.mark.parametrize('error', [ServerSelectionTimeoutError('private uri'), OperationFailure('private')])
def test_mongo_errors_controlled(repository, method, collection, operation, args, error):
    getattr(getattr(repository, collection), operation).side_effect = error
    with pytest.raises(AuthUnavailable):
        getattr(repository, method)(*args)


def test_user_lookup_update_and_session_delete(repository):
    repository.find_user_by_email('a@example.com')
    repository.users.find_one.assert_called_with({'email_normalized': 'a@example.com'})
    repository.find_user_by_id('id')
    repository.users.find_one.assert_called_with({'_id': 'id'})
    repository.update_user('id', {'bio': 'x'})
    repository.users.find_one_and_update.assert_called_once_with(
        {'_id': 'id'}, {'$set': {'bio': 'x'}}, return_document=ReturnDocument.AFTER)
    repository.delete_session('hashed')
    repository.sessions.delete_one.assert_called_once_with({'token_hash': 'hashed'})


def test_login_attempt_queries(repository):
    since = datetime.now(timezone.utc)
    first = datetime.now(timezone.utc)
    repository.attempts.find.return_value.sort.return_value = [{'created_at': first}]
    assert repository.failed_logins('key', since) == [first]
    repository.attempts.find.assert_called_once_with({'key_hash': 'key', 'created_at': {'$gt': since}})
    repository.attempts.find.return_value.sort.assert_called_once_with('created_at', 1)
    repository.record_failed_login('key', first, since)
    repository.attempts.insert_one.assert_called_once_with(
        {'key_hash': 'key', 'created_at': first, 'expires_at': since})
    repository.clear_failed_logins('key')
    repository.attempts.delete_many.assert_called_once_with({'key_hash': 'key'})


@pytest.mark.parametrize('method,args', [
    ('find_session', ({'$ne': None},)),
    ('find_user_by_email', ({'$regex': '.*'},)),
    ('find_user_by_id', ({'$ne': None},)),
    ('delete_session', ({'$ne': None},)),
    ('update_user', ('id', {'$set': {'role': 'admin'}})),
    ('update_user', ('id', {'role': 'admin'})),
    ('update_user', ('id', {'bio': {'$ne': None}})),
])
def test_repository_rejects_operator_injection_before_driver(repository, method, args):
    with pytest.raises(ValueError):
        getattr(repository, method)(*args)
    repository.users.find_one.assert_not_called()
    repository.users.find_one_and_update.assert_not_called()
    repository.sessions.find_one.assert_not_called()
    repository.sessions.delete_one.assert_not_called()
