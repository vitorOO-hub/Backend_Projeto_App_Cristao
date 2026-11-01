"""Role changes revoke generations, including legacy and partially cleaned sessions."""
from copy import deepcopy
from importlib import import_module
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure

from app.api.auth import digest
from app.repositories.auth import MongoAuthRepository
from app.core.errors import AuthUnavailable

auth_module = import_module('app.api.auth')


@pytest.mark.parametrize('command', ['create-admin', 'set-role'])
def test_promotion_revokes_every_existing_cookie_and_new_login_works(world, monkeypatch, command):
    actor = world.user('Maria')
    email = actor.user['email_normalized']
    cookie = actor.client.get_cookie('app_session').value
    assert actor.get('/api/me').status_code == 200
    args = [command, email] + (['admin'] if command == 'set-role' else [])
    result = world.app.test_cli_runner().invoke(args=args)
    assert result.exit_code == 0, result.output
    assert actor.get('/api/me').status_code == 401
    assert actor.get('/api/moderation/videos').status_code == 401
    monkeypatch.setattr(auth_module, 'check_password_hash', lambda *_: True)
    csrf = actor.get('/api/auth/csrf').json['data']['csrf_token']
    response = actor.client.post('/api/auth/login', json={'email': email, 'password': 'synthetic'},
                                 headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    assert response.json['data']['user']['role'] == 'admin'
    new_cookie = actor.client.get_cookie('app_session').value
    assert new_cookie != cookie
    assert world.auth.sessions[digest(new_cookie)]['auth_version'] == 1
    assert actor.get('/api/moderation/videos').status_code == 200


def test_demoted_admin_must_log_in_again(world, monkeypatch):
    actor = world.admin()
    email = actor.user['email_normalized']
    result = world.app.test_cli_runner().invoke(args=['set-role', email, 'user'])
    assert result.exit_code == 0, result.output
    assert actor.get('/api/moderation/videos').status_code == 401
    monkeypatch.setattr(auth_module, 'check_password_hash', lambda *_: True)
    csrf = actor.get('/api/auth/csrf').json['data']['csrf_token']
    response = actor.client.post('/api/auth/login', json={'email': email, 'password': 'synthetic'},
                                 headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    assert response.json['data']['user']['role'] == 'user'
    assert actor.get('/api/me').status_code == 200
    assert actor.get('/api/moderation/videos').status_code == 403


@pytest.mark.parametrize('role', ['user', 'admin'])
def test_version_revokes_cookie_even_when_session_cleanup_fails(world, role):
    actor = world.user(role=role)
    world.auth.cleanup_unavailable = True
    sessions = deepcopy(world.auth.sessions)
    target = 'admin' if role == 'user' else 'user'
    with pytest.raises(AuthUnavailable):
        world.auth.set_role(actor.id, target)
    assert world.auth.sessions == sessions
    assert world.auth.users[actor.user['email_normalized']]['role'] == target
    assert actor.get('/api/me').status_code == 401
    assert actor.get('/api/moderation/videos').status_code == 401
    assert actor.patch('/api/me', json={'bio': 'must not be written'}).status_code == 403
    assert not world.auth.users[actor.user['email_normalized']]['bio']


def test_legacy_session_remains_valid_until_account_generation_changes(world):
    actor = world.user()
    assert 'auth_version' not in actor.user
    assert 'auth_version' not in next(iter(world.auth.sessions.values()))
    assert actor.get('/api/me').status_code == 200
    world.auth.users[actor.user['email_normalized']]['auth_version'] = 1
    assert actor.get('/api/me').status_code == 401


def test_login_started_before_promotion_cannot_inherit_admin_access(world, monkeypatch):
    actor = world.user()
    email = actor.user['email_normalized']
    client = world.app.test_client()
    csrf = client.get('/api/auth/csrf').json['data']['csrf_token']

    def promote_during_password_check(*_):
        world.auth.set_role(actor.id, 'admin')
        return True

    monkeypatch.setattr(auth_module, 'check_password_hash', promote_during_password_check)
    response = client.post('/api/auth/login', json={'email': email, 'password': 'synthetic'},
                           headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    old_snapshot = world.auth.sessions[digest(client.get_cookie('app_session').value)]
    assert old_snapshot['auth_version'] == 0
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/moderation/videos').status_code == 401


@pytest.mark.parametrize('path,method', [('/api/me', 'get'), ('/api/me', 'patch')])
def test_role_change_between_session_and_account_lookup_is_rechecked(world, monkeypatch, path, method):
    actor = world.user()
    lookup = world.auth.find_user_by_id
    calls = 0

    def change_on_second_lookup(user_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            world.auth.users[actor.user['email_normalized']].update(role='admin', auth_version=1)
        return lookup(user_id)

    monkeypatch.setattr(world.auth, 'find_user_by_id', change_on_second_lookup)
    response = actor.send(method, path, **({'json': {'bio': 'blocked'}} if method == 'patch' else {}))
    assert response.status_code == 401
    assert not world.auth.users[actor.user['email_normalized']]['bio']


def test_optional_identity_rechecks_generation_before_showing_pending_video(world, monkeypatch):
    author, admin = world.user(), world.admin()
    video = world.video(author, status='pending')
    lookup = world.auth.find_user_by_id
    calls = 0

    def demote_on_second_lookup(user_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            world.auth.users[admin.user['email_normalized']].update(role='user', auth_version=1)
        return lookup(user_id)

    monkeypatch.setattr(world.auth, 'find_user_by_id', demote_on_second_lookup)
    assert admin.get(f'/api/videos/{video["_id"]}').status_code == 404


@pytest.mark.parametrize('version', [-1, True, '1', None])
def test_malformed_stored_generation_is_never_authenticated(world, version):
    actor = world.user()
    world.auth.users[actor.user['email_normalized']]['auth_version'] = version
    next(iter(world.auth.sessions.values()))['auth_version'] = version
    assert actor.get('/api/me').status_code == 401


def test_mongo_role_and_revocation_are_one_write_and_cleanup_selects_only_older_generations():
    database = {'users': MagicMock(), 'sessions': MagicMock(), 'login_attempts': MagicMock()}
    repository = MongoAuthRepository(database)
    user_id = ObjectId()
    database['users'].find_one_and_update.return_value = {'_id': user_id, 'auth_version': 3}
    assert repository.set_role(user_id, 'admin') is True
    database['users'].find_one_and_update.assert_called_once_with(
        {'_id': user_id}, {'$set': {'role': 'admin'}, '$inc': {'auth_version': 1}},
        return_document=ReturnDocument.AFTER)
    database['sessions'].delete_many.assert_called_once_with(
        {'user_id': user_id, '$or': [{'auth_version': {'$exists': False}},
                                    {'auth_version': {'$lt': 3}}]})


def test_mongo_cleanup_failure_occurs_only_after_atomic_revocation():
    database = {'users': MagicMock(), 'sessions': MagicMock(), 'login_attempts': MagicMock()}
    repository = MongoAuthRepository(database)
    user_id = ObjectId()
    database['users'].find_one_and_update.return_value = {'_id': user_id, 'auth_version': 1}
    database['sessions'].delete_many.side_effect = OperationFailure('private')
    with pytest.raises(AuthUnavailable):
        repository.set_role(user_id, 'user')
    database['users'].find_one_and_update.assert_called_once()


def test_mongo_missing_account_does_not_clean_other_sessions():
    database = {'users': MagicMock(), 'sessions': MagicMock(), 'login_attempts': MagicMock()}
    repository = MongoAuthRepository(database)
    database['users'].find_one_and_update.return_value = None
    assert repository.set_role(ObjectId(), 'user') is False
    database['sessions'].delete_many.assert_not_called()
