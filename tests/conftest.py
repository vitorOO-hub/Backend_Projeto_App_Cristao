import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
import requests

from app import create_app
from app.core.errors import AuthUnavailable, EmailConflict, UsernameConflict


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Os testes não devem acessar a rede.")
    monkeypatch.setattr(requests, "get", forbidden)


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "APP_ENV": "testing", "MONGODB_URI": "",
                       "CACHE_SQLITE_PATH": tmp_path / "cache.sqlite3"})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def install(status=200, payload=None, raw=None):
        data = payload if payload is not None else {
            "reference": "John 3:16", "text": "Texto de teste.", "translation_id": "almeida"
        }

        class Response:
            status_code = status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def iter_content(self, chunk_size):
                yield raw if raw is not None else json.dumps(data).encode()

        def get(*args, **kwargs):
            calls.append((args, kwargs))
            return Response()

        monkeypatch.setattr(requests, "get", get)
        return calls

    return install


class FakeAuthRepository:
    def __init__(self):
        self.sessions = {}
        self.users = {}
        self.attempts = {}
        self.unavailable = False
        self.race = False
        self.cleanup_unavailable = False

    def check(self):
        if self.unavailable:
            raise AuthUnavailable()

    def initialize(self):
        self.check()

    def find_session(self, key):
        self.check()
        return deepcopy(self.sessions.get(key))

    def insert_session(self, document):
        self.check()
        self.sessions[document['token_hash']] = deepcopy(document)

    def rotate_csrf(self, key, csrf_hash, now):
        self.check()
        if self.sessions[key]['expires_at'] <= now:
            return False
        self.sessions[key]['csrf_hash'] = csrf_hash
        return True

    def insert_user(self, document):
        self.check()
        if self.race or document['email_normalized'] in self.users:
            raise EmailConflict()
        username = document.get('username_normalized')
        if username is not None and self.find_user_by_username(username):
            raise UsernameConflict()
        self.users[document['email_normalized']] = deepcopy(document)
        return document

    def find_user_by_email(self, email):
        self.check()
        return deepcopy(self.users.get(email))

    def find_user_by_username(self, username):
        self.check()
        return deepcopy(next((u for u in self.users.values() if u.get('username_normalized') == username), None))

    def find_user_by_id(self, user_id):
        self.check()
        return deepcopy(next((u for u in self.users.values() if u['_id'] == user_id), None))

    def find_users_by_ids(self, user_ids):
        self.check()
        user_ids = set(user_ids)
        return {u['_id']: deepcopy(u) for u in self.users.values() if u['_id'] in user_ids}

    def promote_user(self, email):
        self.check()
        if email not in self.users:
            return False
        return self.set_role(self.users[email]['_id'], 'admin')

    def update_user(self, user_id, fields):
        self.check()
        user = next((u for u in self.users.values() if u['_id'] == user_id), None)
        if user is None:
            return None
        user.update(fields)
        return deepcopy(user)

    def set_role(self, user_id, role):
        self.check()
        user = next((u for u in self.users.values() if u['_id'] == user_id), None)
        if user is None:
            return False
        user.update(role=role, auth_version=user.get('auth_version', 0) + 1)
        if self.cleanup_unavailable:
            raise AuthUnavailable()
        self.sessions = {k: s for k, s in self.sessions.items()
                         if s.get('user_id') != user_id or s.get('auth_version', 0) >= user['auth_version']}
        return True

    def delete_session(self, key):
        self.check()
        self.sessions.pop(key, None)

    def failed_logins(self, key, since):
        self.check()
        return sorted(at for at in self.attempts.get(key, []) if at > since)

    def record_failed_login(self, key, at, expires_at):
        self.check()
        self.attempts.setdefault(key, []).append(at)

    def clear_failed_logins(self, key):
        self.check()
        self.attempts.pop(key, None)


@pytest.fixture
def setup(tmp_path):
    repo = FakeAuthRepository()
    time = [datetime(2026, 10, 22, tzinfo=timezone.utc)]
    app = create_app({'TESTING': True, 'APP_ENV': 'testing', 'MONGODB_URI': '',
                      'CACHE_SQLITE_PATH': tmp_path / 'cache', 'AUTH_REPOSITORY': repo,
                      'AUTH_CLOCK': lambda: time[0], 'SESSION_COOKIE_SECURE': False})
    return app, app.test_client(), repo, time


@pytest.fixture
def world(tmp_path):
    from world import World
    return World(tmp_path)
