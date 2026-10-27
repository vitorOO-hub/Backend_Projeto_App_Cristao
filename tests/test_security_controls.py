from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from app import create_app
from app.errors import AuthUnavailable
from app.security import (MongoRateLimits, SQLiteRateLimits, configure_security,
                          consume_budget)

JWT_SECRET = 's' * 32


def csrf(client):
    response = client.get('/api/auth/csrf')
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json['data']['csrf_token']}


def test_anonymous_session_creation_is_bounded(setup):
    app, _, repo, _ = setup
    statuses = [app.test_client().get('/api/auth/csrf').status_code for _ in range(31)]
    assert statuses == [200] * 30 + [429]
    assert len(repo.sessions) == 30


def test_signup_budget_precedes_password_hash_and_write(setup):
    _, client, repo, _ = setup
    headers = csrf(client)
    with patch('app.auth.generate_password_hash', return_value='synthetic') as hashing:
        for n in range(5):
            assert client.post('/api/users', headers=headers, json={
                'name': 'Synthetic user', 'email': f'audit{n}@example.com',
                'password': 'audit-password-12345'}).status_code == 201
        blocked = client.post('/api/users', headers=headers, json={
            'name': 'Synthetic user', 'email': 'blocked@example.com',
            'password': 'audit-password-12345'})
    assert blocked.status_code == 429
    assert int(blocked.headers['Retry-After']) > 0
    assert hashing.call_count == 5
    assert len(repo.users) == 5


def test_large_auth_body_is_rejected_before_user_write(setup):
    _, client, repo, _ = setup
    response = client.post('/api/users', headers=csrf(client), json={
        'name': ' ' * (512 * 1024) + 'Synthetic user',
        'email': 'large@example.com', 'password': 'audit-password-12345'})
    assert response.status_code == 413
    assert not repo.users


def test_atomic_budget_is_shared_between_instances_and_threads(tmp_path):
    path = tmp_path / 'limits.db'
    stores = [SQLiteRateLimits(path), SQLiteRateLimits(path)]
    stores[0].initialize()
    at = datetime.now(timezone.utc)
    expires = at + timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(lambda n: stores[n % 2].reserve('bucket', 10, expires, at),
                                 range(24)))
    assert sum(admitted) == 10
    assert stores[1].reserve('different-bucket', 10, expires, at)


def test_concurrent_login_never_checks_more_passwords_than_budget(setup):
    app, client, _, _ = setup
    headers = csrf(client)
    cookie = client.get_cookie('app_session').value

    def attempt(_):
        concurrent = app.test_client()
        concurrent.set_cookie('app_session', cookie)
        return concurrent.post('/api/auth/login', headers=headers,
            json={'email': 'unknown@example.com', 'password': 'audit-wrong-password'}).status_code

    with patch('app.auth.dummy_hash', return_value='synthetic'), \
         patch('app.auth.check_password_hash', return_value=False) as check:
        with ThreadPoolExecutor(max_workers=12) as pool:
            codes = list(pool.map(attempt, range(12)))
    assert codes.count(401) == 10
    assert codes.count(429) == 2
    assert check.call_count == 10


def test_untrusted_forwarded_headers_do_not_change_identity(setup):
    app, client, _, _ = setup
    for _ in range(30):
        assert client.get('/api/auth/csrf',
            headers={'X-Forwarded-For': f'192.0.2.{_ + 1}'}).status_code == 200
    assert client.get('/api/auth/csrf', headers={'X-Forwarded-For': '198.51.100.1'}).status_code == 429


def test_trusted_proxy_separates_client_budgets(setup):
    app, client, _, _ = setup
    app.config['TRUSTED_PROXY_IPS'] = '127.0.0.1'
    configure_security(app)
    headers = csrf(client)
    for _ in range(10):
        assert client.post('/api/auth/login',
            headers={**headers, 'X-Forwarded-For': '192.0.2.1'},
            json={'email': 'target@example.com', 'password': 'audit-password-12345'}).status_code == 401
    assert client.post('/api/auth/login',
        headers={**headers, 'X-Forwarded-For': '198.51.100.1'},
        json={'email': 'target@example.com', 'password': 'audit-password-12345'}).status_code == 401


def test_exhausted_client_does_not_consume_global_budget(setup):
    app, _, _, clock = setup
    with app.test_request_context():
        for _ in range(30):
            consume_budget('csrf', '127.0.0.1', 30, 60)
    for _ in range(5):
        assert app.test_client().get('/api/auth/csrf').status_code == 429
    with app.test_request_context():
        for _ in range(1000):
            consume_budget('public-global', 'all', 1000, 60)
    clock[0] += timedelta(minutes=1)
    assert app.test_client().get('/api/auth/csrf').status_code == 200


def test_failed_limiter_fails_closed_and_health_remains_available(setup):
    app, client, _, _ = setup
    with patch.object(app.extensions['rate_limits'], 'reserve', side_effect=AuthUnavailable()):
        assert client.get('/api/auth/csrf').status_code == 503
        assert client.get('/api/health').status_code == 200


def test_mongo_duplicate_bucket_uses_capped_atomic_retry():
    collection = MagicMock()
    collection.find_one_and_update.side_effect = [DuplicateKeyError('bucket'), None]
    store = MongoRateLimits(collection)
    assert store.reserve('key', 10, datetime.now(timezone.utc)) is False
    assert collection.find_one_and_update.call_args.kwargs['upsert'] is False
    selector = collection.find_one_and_update.call_args.args[0]
    assert selector == {'_id': 'key', 'count': {'$lt': 10}}
    collection.find_one_and_update.side_effect = OperationFailure('private database information')
    with pytest.raises(AuthUnavailable):
        store.reserve('key', 10, datetime.now(timezone.utc))


@pytest.mark.parametrize('uri', [
    'mongodb://localhost:27017', 'mongodb://localhost:27017/?tls=false',
    'mongodb+srv://example.com/?tls=false',
    'mongodb+srv://example.com/?tlsAllowInvalidCertificates=true',
    'mongodb+srv://example.com/?tlsAllowInvalidHostnames=true',
    'mongodb+srv://example.com/?tlsInsecure=true',
    'mongodb+srv://example.com/?tls=true&TLS=false',
])
def test_production_rejects_insecure_transport_before_connecting(uri):
    with patch('app.MongoClient') as mongo, pytest.raises(ValueError, match='TLS'):
        create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                    'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': uri, 'JWT_SECRET': JWT_SECRET})
    mongo.assert_not_called()


def test_production_requires_https_origin_before_connecting():
    with patch('app.MongoClient') as mongo, pytest.raises(ValueError, match='HTTPS'):
        create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'http://example.com', 'JWT_SECRET': JWT_SECRET,
                    'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': 'mongodb+srv://example.com'})
    mongo.assert_not_called()


def test_production_headers_host_and_setup_credential(monkeypatch):
    monkeypatch.delenv('MONGODB_SETUP_URI', raising=False)
    with patch('app.MongoClient') as mongo:
        app = create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                          'TRUSTED_PROXY_IPS': '127.0.0.1', 'JWT_SECRET': JWT_SECRET,
                          'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': 'mongodb+srv://example.com'})
        assert mongo.call_count == 1
        response = app.test_client().get('/api/health', base_url='https://example.com')
        assert response.status_code == 200
        assert response.headers['Strict-Transport-Security'] == 'max-age=31536000'
        assert app.test_client().get('/api/health', base_url='https://evil.example').status_code == 400
        result = app.test_cli_runner().invoke(args=['init-auth'])
        assert result.exit_code != 0
        # Missing setup credential must not cause initialization with runtime privileges.
        assert mongo.call_count == 1


def test_production_index_setup_uses_separate_credential(monkeypatch):
    monkeypatch.setenv('MONGODB_SETUP_URI', 'mongodb+srv://setup.example.com')
    runtime, setup_client = MagicMock(), MagicMock()
    setup_client.__enter__.return_value = setup_client
    with patch('app.MongoClient', side_effect=[runtime, setup_client]) as mongo:
        app = create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                          'TRUSTED_PROXY_IPS': '127.0.0.1', 'JWT_SECRET': JWT_SECRET,
                          'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': 'mongodb+srv://runtime.example.com'})
        result = app.test_cli_runner().invoke(args=['init-auth'])
    assert result.exit_code == 0
    assert mongo.call_args_list[1].args[0] == 'mongodb+srv://setup.example.com'
    runtime.__getitem__.return_value.__getitem__.return_value.create_index.assert_not_called()
    assert setup_client.__getitem__.return_value.__getitem__.return_value.create_index.called


def test_production_rejects_missing_trusted_proxy_before_connecting():
    with patch('app.MongoClient') as mongo, pytest.raises(ValueError, match='TRUSTED_PROXY_IPS'):
        create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                    'TRUSTED_PROXY_IPS': '', 'SESSION_COOKIE_SECURE': True, 'JWT_SECRET': JWT_SECRET,
                    'MONGODB_URI': 'mongodb+srv://example.com'})
    mongo.assert_not_called()


@pytest.mark.parametrize('secret', ['', 'curta'])
def test_production_requires_strong_jwt_secret(secret):
    with patch('app.MongoClient') as mongo, pytest.raises(ValueError, match='JWT_SECRET'):
        create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                    'TRUSTED_PROXY_IPS': '127.0.0.1', 'JWT_SECRET': secret,
                    'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': 'mongodb+srv://example.com'})
    mongo.assert_not_called()


def test_production_set_role_uses_setup_credential(monkeypatch):
    monkeypatch.setenv('MONGODB_SETUP_URI', 'mongodb+srv://setup.example.com')
    runtime, setup_client = MagicMock(), MagicMock()
    setup_client.__enter__.return_value = setup_client
    collection = setup_client.__getitem__.return_value.__getitem__.return_value
    collection.find_one.return_value = {'_id': ObjectId()}
    with patch('app.MongoClient', side_effect=[runtime, setup_client]) as mongo:
        app = create_app({'APP_ENV': 'production', 'APP_ORIGIN': 'https://example.com',
                          'TRUSTED_PROXY_IPS': '127.0.0.1', 'JWT_SECRET': JWT_SECRET,
                          'SESSION_COOKIE_SECURE': True, 'MONGODB_URI': 'mongodb+srv://runtime.example.com'})
        result = app.test_cli_runner().invoke(args=['set-role', 'admin@example.com', 'admin'])
    assert result.exit_code == 0, result.output
    assert mongo.call_args_list[1].args[0] == 'mongodb+srv://setup.example.com'
    assert collection.update_one.call_args.args[1] == {'$set': {'role': 'admin'}}
    collection.delete_many.assert_called_once()
    runtime.__getitem__.return_value.__getitem__.return_value.update_one.assert_not_called()
