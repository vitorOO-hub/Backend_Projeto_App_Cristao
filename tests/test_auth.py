from copy import deepcopy
from datetime import timedelta

import pytest
from werkzeug.security import check_password_hash

from app import create_app
from app.api.auth import digest


def credentials(client):
    response = client.get('/api/auth/csrf')
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json['data']['csrf_token']}


def payload(**changes):
    return {'name': '  Maria Silva ', 'email': ' Maria@Example.com ',
            'password': '  password12345  ', **changes}


def test_registration_and_secrets(setup):
    app, client, repo, time = setup
    headers = credentials(client)
    cookie = client.get_cookie('app_session').value
    response = client.post('/api/users', json=payload(), headers=headers)
    assert response.status_code == 201
    data = response.json['data']
    assert data == {'id': data['id'], 'name': 'Maria Silva', 'username': None, 'email': 'maria@example.com',
                    'role': 'user', 'bio': '', 'created_at': '2026-10-22T00:00:00Z'}
    assert len(data['id']) == 24
    assert response.headers['Location'] == '/api/users/' + data['id']
    assert response.headers['Cache-Control'] == 'no-store'
    user = repo.users['maria@example.com']
    assert user['password_hash'].startswith('scrypt:')
    assert check_password_hash(user['password_hash'], payload()['password'])
    assert not check_password_hash(user['password_hash'], payload()['password'].strip())
    session = repo.sessions[digest(cookie)]
    assert 'user_id' not in session
    assert cookie not in repr(session)
    assert headers['X-CSRF-Token'] not in repr(session)
    assert session['csrf_hash'] == digest(headers['X-CSRF-Token'])
    assert 'password' not in response.text and 'hash' not in response.text


@pytest.mark.parametrize('changes', [
    {'name': 'a'}, {'name': 'x' * 81}, {'email': 'a@b'}, {'email': 'a..b@example.com'},
    {'email': 'a@-bad.com'}, {'email': {'$ne': None}}, {'password': 123456789012},
    {'password': 'a' * 11}, {'password': 'a' * 129}, {'role': 'admin'}, {'extra': 1},
    {'name': None}, {'email': 'a' * 245 + '@example.com'}])
def test_invalid_fields(setup, changes):
    _, client, repo, _ = setup
    response = client.post('/api/users', json=payload(**changes), headers=credentials(client))
    assert response.status_code == 400
    assert not repo.users
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('body', ['{', '[]', 'null', '"text"', '{}'])
def test_invalid_json(setup, body):
    _, client, repo, _ = setup
    response = client.post('/api/users', data=body, content_type='application/json',
                           headers=credentials(client))
    assert response.status_code == 400
    assert not repo.users


def test_non_json(setup):
    _, client, _, _ = setup
    assert client.post('/api/users', data='x', headers=credentials(client)).status_code == 400


def test_duplicate_and_race(setup):
    _, client, repo, _ = setup
    headers = credentials(client)
    assert client.post('/api/users', json=payload(), headers=headers).status_code == 201
    assert client.post('/api/users', json=payload(email='MARIA@example.com'), headers=headers).status_code == 409
    repo.race = True
    assert client.post('/api/users', json=payload(email='other@example.com'), headers=headers).status_code == 409


def test_missing_invalid_and_cross_session_csrf(setup):
    app, client, repo, _ = setup
    assert client.post('/api/users', json=payload()).status_code == 403
    first = credentials(client)
    assert client.post('/api/users', json=payload()).status_code == 403
    assert client.post('/api/users', json=payload(), headers={'X-CSRF-Token': 'é'}).status_code == 403
    other = app.test_client()
    credentials(other)
    assert other.post('/api/users', json=payload(), headers=first).status_code == 403
    no_cookie = app.test_client()
    assert no_cookie.post('/api/users', json=payload(), headers=first).status_code == 403
    assert not repo.users


def test_expiry_rotation_and_absolute_lifetime(setup):
    _, client, repo, time = setup
    old = credentials(client)
    original_cookie = client.get_cookie('app_session').value
    original = deepcopy(repo.sessions[digest(original_cookie)])
    time[0] += timedelta(minutes=10)
    new = credentials(client)
    assert old != new
    assert client.get_cookie('app_session').value == original_cookie
    assert repo.sessions[digest(original_cookie)]['expires_at'] == original['expires_at']
    assert client.post('/api/users', json=payload(), headers=old).status_code == 403
    time[0] = original['expires_at']
    assert client.post('/api/users', json=payload(), headers=new).status_code == 403
    credentials(client)
    assert client.get_cookie('app_session').value != original_cookie


def test_origin_and_no_store(setup):
    _, client, repo, _ = setup
    assert client.get('/api/auth/csrf', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert not repo.sessions
    headers = credentials(client)
    csrf_hash = next(iter(repo.sessions.values()))['csrf_hash']
    denied = client.get('/api/auth/csrf', headers={'Origin': 'null'})
    assert denied.status_code == 403
    assert denied.headers['Cache-Control'] == 'no-store'
    assert next(iter(repo.sessions.values()))['csrf_hash'] == csrf_hash
    assert client.post('/api/users', json=payload(), headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/users', json=payload(), headers={**headers, 'Origin': 'http://localhost:5173'}).status_code == 201


def test_cookie_flags(setup):
    app, client, _, _ = setup
    cookie = client.get('/api/auth/csrf').headers['Set-Cookie']
    for expected in ('HttpOnly', 'SameSite=Lax', 'Path=/', 'Max-Age=1800'):
        assert expected in cookie
    assert 'Domain=' not in cookie
    app.config['SESSION_COOKIE_SECURE'] = True
    cookie = app.test_client().get('/api/auth/csrf').headers['Set-Cookie']
    assert 'Secure;' in cookie


def test_unavailable_and_cli(setup):
    app, client, repo, _ = setup
    headers = credentials(client)
    repo.unavailable = True
    for response in (client.get('/api/auth/csrf'), client.post('/api/users', json=payload(), headers=headers)):
        assert response.status_code == 503
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.json['error']['code'] == 'auth_unavailable'
    result = app.test_cli_runner().invoke(args=['init-auth'])
    assert result.exit_code == 1
    assert 'Traceback' not in result.output
    repo.unavailable = False
    assert app.test_cli_runner().invoke(args=['init-auth']).exit_code == 0


def test_no_uri_has_no_account_fallback(app):
    assert app.test_client().get('/api/auth/csrf').status_code == 503
    assert app.test_cli_runner().invoke(args=['init-auth']).exit_code == 1


@pytest.mark.parametrize('config', [
    {'APP_ENV': 'production', 'SESSION_COOKIE_SECURE': False},
    {'APP_ENV': 'development', 'APP_ORIGIN': 'http://example.com', 'SESSION_COOKIE_SECURE': False},
    {'APP_ORIGIN': 'https://example.com/'}, {'ANONYMOUS_SESSION_TTL_SECONDS': 3600}])
def test_unsafe_configuration_rejected(config):
    with pytest.raises(ValueError):
        create_app({'MONGODB_URI': '', **config})


@pytest.mark.parametrize('field', ['name', 'email', 'password'])
def test_invalid_unicode_rejected_before_persistence(setup, field):
    _, client, repo, _ = setup
    values = payload()
    values[field] += chr(0xd800)
    response = client.post('/api/users', json=values, headers=credentials(client))
    assert response.status_code == 400
    assert response.json['error']['code'] == 'validation_error'
    assert not repo.users


def test_set_role_cli_promotes_and_ends_sessions(setup):
    app, client, repo, _ = setup
    client.post('/api/users', json=payload(), headers=credentials(client))
    login = client.post('/api/auth/login', json={'email': 'maria@example.com', 'password': '  password12345  '},
                        headers=credentials(client))
    assert login.status_code == 200 and client.get('/api/me').json['data']['role'] == 'user'
    cli = app.test_cli_runner()
    result = cli.invoke(args=['set-role', ' Maria@Example.com ', 'admin'])
    assert result.exit_code == 0, result.output
    assert repo.users['maria@example.com']['role'] == 'admin'
    assert client.get('/api/me').status_code == 401  # a sessão comum não vira administrativa
    assert cli.invoke(args=['set-role', 'maria@example.com', 'user']).exit_code == 0
    assert repo.users['maria@example.com']['role'] == 'user'
    missing = cli.invoke(args=['set-role', 'ninguem@example.com', 'admin'])
    assert missing.exit_code == 1 and 'Conta não encontrada' in missing.output
    assert cli.invoke(args=['set-role', 'maria@example.com', 'root']).exit_code == 2
    repo.unavailable = True
    down = cli.invoke(args=['set-role', 'maria@example.com', 'admin'])
    assert down.exit_code == 1 and 'Traceback' not in down.output
