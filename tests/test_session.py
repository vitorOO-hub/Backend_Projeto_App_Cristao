from datetime import timedelta

import jwt
import pytest
from bson import ObjectId

from app.api.auth import digest, issue_token

EMAIL = 'maria@example.com'
PASSWORD = 'password12345'


def csrf(client):
    response = client.get('/api/auth/csrf')
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json['data']['csrf_token']}


def register(client, email=EMAIL):
    body = {'name': 'Maria Silva', 'email': email, 'password': PASSWORD}
    response = client.post('/api/users', json=body, headers=csrf(client))
    assert response.status_code == 201
    return response.json['data']


def login(client, email=EMAIL, password=PASSWORD, headers=None):
    headers = headers or csrf(client)
    return client.post('/api/auth/login', json={'email': email, 'password': password}, headers=headers)


@pytest.fixture
def logged(setup):
    app, client, repo, time = setup
    user = register(client)
    response = login(client)
    assert response.status_code == 200
    headers = {'X-CSRF-Token': response.json['data']['csrf_token']}
    return client, repo, time, user, headers


def test_login_rotates_session_and_returns_user(setup):
    _, client, repo, time = setup
    user = register(client)
    anonymous = client.get_cookie('app_session').value
    response = login(client)
    assert response.status_code == 200
    data = response.json['data']
    assert data['user'] == user
    assert data['csrf_token']
    assert response.headers['Cache-Control'] == 'no-store'
    cookie = client.get_cookie('app_session').value
    assert cookie != anonymous
    assert digest(anonymous) not in repo.sessions
    session = repo.sessions[digest(cookie)]
    assert len(repo.sessions) == 1
    assert str(session['user_id']) == user['id']
    assert session['expires_at'] - time[0] == timedelta(hours=12)
    assert session['csrf_hash'] == digest(data['csrf_token'])
    for flag in ('HttpOnly', 'SameSite=Lax', 'Path=/', 'Max-Age=43200'):
        assert flag in response.headers['Set-Cookie']
    assert 'password' not in response.text and 'hash' not in response.text


def test_login_normalizes_email_and_keeps_password_exact(setup):
    _, client, _, _ = setup
    register(client)
    assert login(client, email='  MARIA@Example.com ').status_code == 200
    assert login(client, password=PASSWORD + ' ').status_code == 401


def test_login_failure_is_generic(setup):
    _, client, repo, _ = setup
    register(client)
    wrong = login(client, password='wrong-password-123')
    unknown = login(client, email='nobody@example.com')
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json == unknown.json
    assert wrong.json['error']['code'] == 'invalid_credentials'
    assert 'user_id' not in next(iter(repo.sessions.values()))


def register_handle(client, username=' Maria.Silva ', email=EMAIL):
    body = {'name': 'Maria Silva', 'username': username, 'email': email, 'password': PASSWORD}
    return client.post('/api/users', json=body, headers=csrf(client))


def test_login_by_email_or_username(setup):
    _, client, repo, _ = setup
    response = register_handle(client)
    assert response.status_code == 201
    assert response.json['data']['username'] == 'maria.silva'
    for identifier in ('maria.silva', ' MARIA.Silva ', EMAIL, ' Maria@Example.com '):
        response = client.post('/api/auth/login', json={'login': identifier, 'password': PASSWORD},
                               headers=csrf(client))
        assert response.status_code == 200, identifier
        assert response.json['data']['user']['username'] == 'maria.silva'
    wrong = client.post('/api/auth/login', json={'login': 'maria.silva', 'password': 'wrong-password-123'},
                        headers=csrf(client))
    unknown = client.post('/api/auth/login', json={'login': 'ninguem', 'password': PASSWORD},
                          headers=csrf(client))
    invalid = client.post('/api/auth/login', json={'login': '..', 'password': PASSWORD}, headers=csrf(client))
    assert wrong.status_code == unknown.status_code == invalid.status_code == 401
    assert wrong.json == unknown.json == invalid.json
    assert client.post('/api/auth/login', json={'login': EMAIL, 'email': EMAIL, 'password': PASSWORD},
                       headers=csrf(client)).status_code == 400


def test_username_is_unique_and_optional(setup):
    _, client, repo, _ = setup
    assert register_handle(client).status_code == 201
    conflict = register_handle(client, username='maria.SILVA', email='outra@example.com')
    assert conflict.status_code == 409
    assert conflict.json['error']['code'] == 'username_conflict'
    # Sem username o corpo antigo continua valendo, e duas contas sem username não colidem.
    assert register(client, email='a@example.com')['username'] is None
    assert register(client, email='b@example.com')['username'] is None


@pytest.mark.parametrize('username', [
    'ab', 'a' * 31, '.maria', 'maria.', 'ma..ria', 'maria silva', 'maria@x', 'maria-silva', 'ñandu',
    {'$ne': None}, 123, None])
def test_invalid_username(setup, username):
    _, client, repo, _ = setup
    assert register_handle(client, username=username).status_code == 400
    assert not repo.users


def test_login_limit_is_shared_by_email_and_username(setup):
    _, client, _, _ = setup
    register_handle(client)
    for n in range(10):
        identifier = EMAIL if n % 2 else 'maria.silva'
        assert client.post('/api/auth/login', json={'login': identifier, 'password': 'wrong-password-123'},
                           headers=csrf(client)).status_code == 401
    assert client.post('/api/auth/login', json={'login': 'maria.silva', 'password': PASSWORD},
                       headers=csrf(client)).status_code == 429


def test_login_requires_csrf_and_valid_origin(setup):
    _, client, _, _ = setup
    register(client)
    body = {'email': EMAIL, 'password': PASSWORD}
    assert client.post('/api/auth/login', json=body).status_code == 403
    headers = csrf(client)
    assert client.post('/api/auth/login', json=body,
                       headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/auth/login', json=body, headers=headers).status_code == 200


@pytest.mark.parametrize('body', [
    {'email': EMAIL}, {'email': EMAIL, 'password': PASSWORD, 'role': 'admin'},
    {'email': {'$ne': None}, 'password': PASSWORD}, {'email': EMAIL, 'password': 1234567890123},
    {'email': EMAIL, 'password': PASSWORD + chr(0xd800)}, [], None])
def test_login_invalid_body(setup, body):
    _, client, repo, _ = setup
    register(client)
    response = client.post('/api/auth/login', json=body, headers=csrf(client))
    assert response.status_code == 400
    assert all('user_id' not in s for s in repo.sessions.values())


def test_login_rate_limit(setup):
    _, client, repo, time = setup
    register(client)
    for _ in range(10):
        assert login(client, password='wrong-password-123').status_code == 401
    blocked = login(client)
    assert blocked.status_code == 429
    assert blocked.json['error']['code'] == 'too_many_attempts'
    assert blocked.headers['Retry-After'] == str(15 * 60)
    assert login(client, email='other@example.com').status_code == 401
    time[0] += timedelta(minutes=5)
    assert login(client).headers['Retry-After'] == str(10 * 60)
    time[0] += timedelta(minutes=10)
    assert login(client).status_code == 200


def test_successful_login_does_not_reset_reserved_budget(setup):
    _, client, _, clock = setup
    register(client)
    for _ in range(9):
        login(client, password='wrong-password-123')
    assert login(client).status_code == 200
    assert login(client, password='wrong-password-123').status_code == 429
    clock[0] += timedelta(minutes=15)
    assert login(client).status_code == 200


def test_login_unavailable(setup):
    _, client, repo, _ = setup
    register(client)
    headers = csrf(client)
    repo.unavailable = True
    response = login(client, headers=headers)
    assert response.status_code == 503
    assert response.json['error']['code'] == 'auth_unavailable'


def test_me_requires_login(setup):
    _, client, _, _ = setup
    assert client.get('/api/me').status_code == 401
    register(client)
    assert client.get('/api/me').status_code == 401


def test_me_returns_current_user_without_secrets(logged):
    client, repo, _, user, _ = logged
    response = client.get('/api/me')
    assert response.status_code == 200
    assert response.json['data'] == user
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'password' not in response.text and 'hash' not in response.text
    repo.users[EMAIL]['role'] = 'admin'
    assert client.get('/api/me').json['data']['role'] == 'admin'


def test_session_survives_page_reload(logged):
    client, _, _, user, _ = logged
    headers = csrf(client)
    assert client.get('/api/me').json['data'] == user
    assert client.patch('/api/me', json={'bio': 'Olá'}, headers=headers).status_code == 200


def test_absolute_expiry_without_sliding_renewal(logged):
    client, repo, time, _, _ = logged
    expires = next(iter(repo.sessions.values()))['expires_at']
    time[0] += timedelta(hours=11, minutes=59)
    assert client.get('/api/me').status_code == 200
    assert next(iter(repo.sessions.values()))['expires_at'] == expires
    time[0] = expires
    assert client.get('/api/me').status_code == 401


def test_me_for_deleted_user(logged):
    client, repo, _, _, _ = logged
    repo.users.clear()
    assert client.get('/api/me').status_code == 401


def test_logout_revokes_session(logged):
    client, repo, _, _, headers = logged
    cookie = client.get_cookie('app_session').value
    response = client.post('/api/auth/logout', headers=headers)
    assert response.status_code == 204
    assert response.data == b''
    assert digest(cookie) not in repo.sessions
    assert 'app_session=;' in response.headers['Set-Cookie']
    assert 'Max-Age=0' in response.headers['Set-Cookie']
    assert client.get('/api/me').status_code == 401
    client.set_cookie('app_session', cookie)
    assert client.get('/api/me').status_code == 401
    assert client.post('/api/auth/logout', headers=headers).status_code == 403


def test_logout_requires_csrf_and_authentication(logged, setup):
    client, repo, _, _, headers = logged
    assert client.post('/api/auth/logout').status_code == 403
    assert client.post('/api/auth/logout', headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/me').status_code == 200
    anonymous = setup[0].test_client()
    assert anonymous.post('/api/auth/logout', headers=csrf(anonymous)).status_code == 401


def test_logout_failure_keeps_session(logged):
    client, repo, _, _, headers = logged
    repo.unavailable = True
    response = client.post('/api/auth/logout', headers=headers)
    assert response.status_code == 503
    repo.unavailable = False
    assert client.get('/api/me').status_code == 200


def test_update_profile(logged):
    client, _, _, user, headers = logged
    response = client.patch('/api/me', json={'name': '  Maria Souza ', 'bio': ' Louvor '}, headers=headers)
    assert response.status_code == 200
    assert response.json['data'] == {**user, 'name': 'Maria Souza', 'bio': 'Louvor'}
    assert client.patch('/api/me', json={'bio': ''}, headers=headers).json['data']['bio'] == ''
    assert client.get('/api/me').json['data']['name'] == 'Maria Souza'


@pytest.mark.parametrize('body', [
    {}, [], None, {'name': 'a'}, {'name': 'x' * 81}, {'bio': 'x' * 301}, {'name': None},
    {'bio': 5}, {'email': 'other@example.com'}, {'role': 'admin'}, {'name': 'Maria', 'password': 'x' * 12},
    {'bio': 'x' + chr(0xd800)}])
def test_update_profile_rejects_invalid(logged, body):
    client, repo, _, user, headers = logged
    assert client.patch('/api/me', json=body, headers=headers).status_code == 400
    assert client.get('/api/me').json['data'] == user
    assert repo.users[EMAIL]['role'] == 'user'


def test_update_profile_boundaries(logged):
    client, _, _, _, headers = logged
    assert client.patch('/api/me', json={'name': 'ab', 'bio': 'x' * 300}, headers=headers).status_code == 200
    assert client.patch('/api/me', json={'name': 'x' * 80}, headers=headers).status_code == 200


def test_update_profile_requires_csrf_and_login(logged, setup):
    client, _, _, _, headers = logged
    assert client.patch('/api/me', json={'bio': 'x'}).status_code == 403
    anonymous = setup[0].test_client()
    assert anonymous.patch('/api/me', json={'bio': 'x'}, headers=csrf(anonymous)).status_code == 401


def test_public_profile(logged, setup):
    client, _, _, user, _ = logged
    anonymous = setup[0].test_client()
    response = anonymous.get('/api/users/' + user['id'])
    assert response.status_code == 200
    assert response.json['data'] == {key: user[key] for key in ('id', 'name', 'username', 'bio', 'created_at')}
    assert 'email' not in response.text and 'role' not in response.text
    assert response.headers['Cache-Control'] == 'no-store'


def test_public_profile_errors(logged):
    client, _, _, _, _ = logged
    assert client.get('/api/users/' + '0' * 24).status_code == 404
    for bad in ('abc', 'G' * 24, '0' * 25, 'A' * 24):
        response = client.get('/api/users/' + bad)
        assert response.status_code == 400
        assert response.json['error']['code'] == 'validation_error'


def test_session_cookie_is_signed_jwt(logged, setup):
    client, _, time, user, _ = logged
    secret = setup[0].config['JWT_SECRET']
    token = client.get_cookie('app_session').value
    assert jwt.get_unverified_header(token) == {'alg': 'HS256', 'typ': 'JWT'}
    claims = jwt.decode(token, secret, algorithms=['HS256'], options={'verify_exp': False})
    assert claims['sub'] == user['id']
    assert claims['iat'] == int(time[0].timestamp())
    assert claims['exp'] - claims['iat'] == 43200
    assert len(claims['jti']) >= 43 and len(token) <= 256
    anonymous = setup[0].test_client()
    anonymous.get('/api/auth/csrf')
    claims = jwt.decode(anonymous.get_cookie('app_session').value, secret, algorithms=['HS256'],
                        options={'verify_exp': False})
    assert 'sub' not in claims and claims['exp'] - claims['iat'] == 1800


def test_forged_or_unstored_jwt_rejected(logged, setup):
    client, repo, time, user, _ = logged
    app = setup[0]
    token = client.get_cookie('app_session').value
    header, _, signature = token.split('.')
    claims = jwt.decode(token, app.config['JWT_SECRET'], algorithms=['HS256'],
                        options={'verify_exp': False})
    other = jwt.encode({**claims, 'sub': str(ObjectId())}, 'k' * 32, algorithm='HS256')
    with app.app_context():
        unstored = issue_token(time[0], 43200, user['id'])
    forged = [
        jwt.encode(claims, 'k' * 32, algorithm='HS256'),
        jwt.encode(claims, None, algorithm='none'),
        '.'.join((header, other.split('.')[1], signature)),
        token[:-10] + ('A' if token[-10] != 'A' else 'B') + token[-9:],
    ]
    for value in forged:
        # Stored on purpose, so only the signature check can refuse it.
        repo.insert_session({**repo.sessions[digest(token)], 'token_hash': digest(value)})
        client.set_cookie('app_session', value)
        assert client.get('/api/me').status_code == 401
    client.set_cookie('app_session', unstored)
    assert client.get('/api/me').status_code == 401
    client.set_cookie('app_session', token)
    assert client.get('/api/me').status_code == 200


def test_jwt_expiry_applies_even_if_record_lives(logged, setup):
    client, repo, time, user, _ = logged
    with setup[0].app_context():
        token = issue_token(time[0], 60, user['id'])
    repo.insert_session({'token_hash': digest(token), 'csrf_hash': digest('x'),
                         'user_id': ObjectId(user['id']), 'created_at': time[0],
                         'expires_at': time[0] + timedelta(hours=12)})
    client.set_cookie('app_session', token)
    assert client.get('/api/me').status_code == 200
    time[0] += timedelta(seconds=60)
    assert client.get('/api/me').status_code == 401
