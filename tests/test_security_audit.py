"""Adversarial HTTP tests with synthetic users and an isolated repository."""
from copy import deepcopy
from datetime import timedelta
import json
import secrets
from urllib.parse import quote
from unittest.mock import Mock

from bson import ObjectId
import pytest
from werkzeug.security import generate_password_hash

from app.api.auth import digest, issue_token
from app.repositories.bible_cache import SQLiteCache


@pytest.fixture(scope='module')
def audit_password_hash():
    return generate_password_hash('audit-password-12345', method='scrypt')


@pytest.fixture
def audit_context(setup, audit_password_hash):
    app, client, repo, clock = setup
    for email in ('owner@example.com', 'other@example.com'):
        repo.insert_user({'_id': ObjectId(), 'email_normalized': email,
                          'name': 'Synthetic user', 'bio': '', 'role': 'user',
                          'password_hash': audit_password_hash, 'created_at': clock[0]})
    owner = repo.users['owner@example.com']['_id']
    with app.app_context():
        cookie = issue_token(clock[0], 43200, owner)
    csrf = secrets.token_urlsafe(32)
    repo.insert_session({'token_hash': digest(cookie), 'csrf_hash': digest(csrf), 'user_id': owner,
                         'created_at': clock[0], 'expires_at': clock[0] + timedelta(hours=12)})
    client.set_cookie('app_session', cookie)
    return app, client, repo, {'X-CSRF-Token': csrf}


BAD_VALUES = [
    {'$ne': None}, {'$gt': ''}, {'$regex': '.*'},
    {'$where': 'return true'}, {'$expr': {'$eq': [1, 1]}},
    {'$or': [{}, {}]}, [], ['owner@example.com'], None, True, 123,
]


@pytest.mark.parametrize('value', BAD_VALUES)
@pytest.mark.parametrize('route', ['/api/users', '/api/auth/login'])
def test_operator_email_rejected_before_user_lookup(audit_context, monkeypatch, value, route):
    _, client, repo, headers = audit_context
    before = deepcopy(repo.users)
    lookup = Mock(wraps=repo.find_user_by_email)
    monkeypatch.setattr(repo, 'find_user_by_email', lookup)
    body = {'email': value, 'password': 'audit-password-12345'}
    if route == '/api/users':
        body['name'] = 'Synthetic user'
    response = client.post(route, json=body, headers=headers)
    assert response.status_code == 400
    lookup.assert_not_called()
    assert repo.users == before


@pytest.mark.parametrize('value', BAD_VALUES)
def test_operator_bio_never_reaches_database_update(audit_context, monkeypatch, value):
    _, client, repo, headers = audit_context
    before = deepcopy(repo.users)
    update = Mock(wraps=repo.update_user)
    monkeypatch.setattr(repo, 'update_user', update)
    assert client.patch('/api/me', json={'bio': value}, headers=headers).status_code == 400
    update.assert_not_called()
    assert repo.users == before


@pytest.mark.parametrize('body', [
    {'$set': {'role': 'admin'}}, {'$unset': {'password_hash': ''}},
    {'$rename': {'bio': 'role'}}, {'role': 'admin'},
    {'name': 'Synthetic user', 'role': 'admin'}, {'user_id': {'$ne': None}},
    {'_id': {'$ne': None}}, {'bio.x': 'admin'},
])
def test_mass_assignment_cannot_change_role_or_target(audit_context, body):
    _, client, repo, headers = audit_context
    before = deepcopy(repo.users)
    assert client.patch('/api/me', json=body, headers=headers).status_code == 400
    assert repo.users == before


def test_profile_update_ignores_forged_identity_headers_and_query(audit_context):
    _, client, repo, headers = audit_context
    other = deepcopy(repo.users['other@example.com'])
    response = client.patch('/api/me?role=admin&user_id=' + str(other['_id']),
                            json={'bio': 'own bio'},
                            headers={**headers, 'X-User-ID': str(other['_id']), 'X-Role': 'admin'})
    assert response.status_code == 200
    assert repo.users['other@example.com'] == other
    assert repo.users['owner@example.com']['role'] == 'user'
    assert repo.users['owner@example.com']['bio'] == 'own bio'


@pytest.mark.parametrize('path,method', [
    ('/api/me', 'DELETE'), ('/api/users', 'DELETE'),
    ('/api/users/{other}', 'DELETE'), ('/api/users/{other}', 'PATCH'),
    ('/api/admin/drop-database', 'POST'), ('/api/db', 'DELETE'),
    ('/api/videos', 'DELETE'), ('/api/moderation/videos', 'DELETE'),
])
def test_no_exposed_destructive_user_or_database_operation(audit_context, path, method):
    _, client, repo, headers = audit_context
    before = deepcopy((repo.users, repo.sessions, repo.attempts))
    path = path.format(other=repo.users['other@example.com']['_id'])
    response = client.open(path, method=method, headers=headers, json={'$where': 'return true'})
    assert response.status_code in (404, 405)
    assert (repo.users, repo.sessions, repo.attempts) == before


@pytest.mark.parametrize('value', [{'$ne': None}, {'$where': 'return true'},
                                  {'$regex': '.*'}, '0' * 25, 'G' * 24])
def test_malformed_public_identifier_cannot_broaden_lookup(audit_context, monkeypatch, value):
    _, client, repo, _ = audit_context
    lookup = Mock(wraps=repo.find_user_by_id)
    monkeypatch.setattr(repo, 'find_user_by_id', lookup)
    text = value if isinstance(value, str) else json.dumps(value)
    response = client.get('/api/users/' + quote(text, safe=''))
    assert response.status_code == 400
    lookup.assert_not_called()


@pytest.mark.parametrize('query', [
    {'reference': 'http://127.0.0.1/'},
    {'reference': 'http://169.254.169.254/latest/meta-data/'},
    {'reference': '../../admin'}, {'reference': '{"$ne":null}'},
    {'reference': 'John 3:16', 'url': 'http://127.0.0.1/'},
    [('reference', 'John 3:16'), ('reference', 'http://127.0.0.1/')],
])
def test_query_injection_and_ssrf_inputs_rejected_without_network(client, query):
    assert client.get('/api/bible/passages', query_string=query).status_code == 400


def test_json_text_remains_literal_profile_data(audit_context):
    _, client, repo, headers = audit_context
    value = '{"$set":{"role":"admin"}}'
    assert client.patch('/api/me', json={'bio': value}, headers=headers).status_code == 200
    assert repo.users['owner@example.com']['bio'] == value
    assert all(user['role'] == 'user' for user in repo.users.values())


def test_sqlite_cache_uses_parameters_for_untrusted_key(tmp_path):
    from datetime import datetime, timezone
    cache = SQLiteCache(tmp_path / 'isolated.db')
    at = datetime.now(timezone.utc)
    expiry = at + timedelta(hours=1)
    key = "'; DROP TABLE passages; --"
    cache.set('protected', {'text': 'keep'}, expiry, at)
    cache.set(key, {'text': 'literal'}, expiry, at)
    assert cache.get(key, at) == {'text': 'literal'}
    assert cache.get('protected', at) == {'text': 'keep'}


def test_cookie_and_csrf_values_cannot_be_mongo_filters(audit_context):
    _, client, repo, headers = audit_context
    before = deepcopy(repo.users)
    assert client.patch('/api/me', json={'bio': 'changed'},
                         headers={'X-CSRF-Token': '{"$ne":null}'}).status_code == 403
    client.set_cookie('app_session', '{"$ne":null}')
    assert client.get('/api/me').status_code == 401
    assert repo.users == before


def test_public_and_error_responses_do_not_expose_password_or_session_hash(audit_context):
    _, client, repo, _ = audit_context
    user = repo.users['other@example.com']
    response = client.get('/api/users/' + str(user['_id']))
    assert response.status_code == 200
    assert set(response.json['data']) == {'id', 'name', 'username', 'bio', 'created_at'}
    assert user['password_hash'] not in response.text
    assert all(key not in response.text for key in repo.sessions)
