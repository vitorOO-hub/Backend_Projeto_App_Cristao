"""Shared, bounded request budgets and fail-closed production configuration."""
import hashlib
import ipaddress
import math
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import parse_qsl, urlsplit

from flask import current_app, request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge

from app.errors import ApiError, AuthUnavailable


class TrustedProxy:
    def __init__(self, app, peers):
        self.app = app
        self.peers = peers
        self.forwarded = ProxyFix(app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        try:
            trusted = ipaddress.ip_address(environ.get('REMOTE_ADDR', '')) in self.peers
        except ValueError:
            trusted = False
        return (self.forwarded if trusted else self.app)(environ, start_response)


def validate_mongo_transport(uri):
    try:
        parts = urlsplit(uri)
        options = {}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            key = key.lower()
            if key in options:
                raise ValueError
            options[key] = value.lower()
        if parts.scheme not in ('mongodb', 'mongodb+srv') or not parts.netloc:
            raise ValueError
        if any(options.get(key) not in (None, 'false') for key in (
                'tlsinsecure', 'tlsallowinvalidcertificates', 'tlsallowinvalidhostnames',
                'tlsdisablecertificaterevocationcheck', 'tlsdisableocspendpointcheck')):
            raise ValueError
        if any(options.get(key) not in (None, 'true') for key in ('tls', 'ssl')):
            raise ValueError
        if parts.scheme == 'mongodb' and options.get('tls', options.get('ssl')) != 'true':
            raise ValueError
    except ValueError:
        raise ValueError('Produção exige URI MongoDB com TLS e validação de certificado.') from None


def configure_security(app):
    if app.config['APP_ENV'] == 'production':
        if urlsplit(app.config['APP_ORIGIN']).scheme != 'https':
            raise ValueError('Produção exige APP_ORIGIN HTTPS.')
        if not app.config['MONGODB_URI']:
            raise ValueError('Produção exige MONGODB_URI; não há fallback para SQLite.')
        validate_mongo_transport(app.config['MONGODB_URI'])
        app.config['TRUSTED_HOSTS'] = [urlsplit(app.config['APP_ORIGIN']).hostname]
    try:
        peers = {ipaddress.ip_address(value.strip()) for value in
                 app.config['TRUSTED_PROXY_IPS'].split(',') if value.strip()}
    except ValueError:
        raise ValueError('TRUSTED_PROXY_IPS deve conter somente endereços IP explícitos.') from None
    if app.config['APP_ENV'] == 'production' and not peers:
        raise ValueError('Produção exige TRUSTED_PROXY_IPS para o proxy de entrada.')
    app.wsgi_app = TrustedProxy(app.wsgi_app, peers)


class MongoRateLimits:
    def __init__(self, collection):
        self.collection = collection

    def initialize(self):
        try:
            self.collection.create_index('expires_at', expireAfterSeconds=0, name='rate_limits_expiry')
        except PyMongoError:
            raise AuthUnavailable() from None

    def reserve(self, key, limit, expires_at, at=None):
        selector = {'_id': key, 'count': {'$lt': limit}}
        change = {'$inc': {'count': 1}, '$setOnInsert': {'expires_at': expires_at}}
        try:
            try:
                row = self.collection.find_one_and_update(
                    selector, change, upsert=True, return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                # Another request created the bucket, or the existing bucket is full.
                row = self.collection.find_one_and_update(
                    selector, {'$inc': {'count': 1}}, upsert=False,
                    return_document=ReturnDocument.AFTER)
            return row is not None
        except PyMongoError:
            raise AuthUnavailable() from None


class SQLiteRateLimits:
    """Development/test only. Atomic reservations are shared between local workers."""
    def __init__(self, path):
        self.path = str(path)

    def initialize(self):
        try:
            with closing(self._connect()):
                pass
        except (OSError, sqlite3.Error):
            raise AuthUnavailable() from None

    def _connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS rate_limits '
                       '(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS rate_limits_expiry ON rate_limits(expires_at)')
        except sqlite3.Error:
            db.close()
            raise
        return db

    def reserve(self, key, limit, expires_at, at=None):
        try:
            with closing(self._connect()) as db, db:
                # Bounded batch prevents request-triggered unbounded cleanup work.
                db.execute('DELETE FROM rate_limits WHERE key IN '
                           '(SELECT key FROM rate_limits WHERE expires_at <= ? LIMIT 100)',
                           ((at or datetime.now(timezone.utc)).timestamp(),))
                row = db.execute(
                    'INSERT INTO rate_limits VALUES (?, 1, ?) '
                    'ON CONFLICT(key) DO UPDATE SET count = count + 1 '
                    'WHERE count < ? RETURNING count',
                    (key, expires_at.timestamp(), limit)).fetchone()
                return row is not None
        except (OSError, sqlite3.Error):
            raise AuthUnavailable() from None


def security_now():
    clock = current_app.config.get('AUTH_CLOCK')
    return clock() if clock else datetime.now(timezone.utc)


def consume_budget(scope, identity, limit, seconds):
    at = security_now()
    window = int(at.timestamp()) // seconds
    expires = datetime.fromtimestamp((window + 1) * seconds, timezone.utc)
    key = hashlib.sha256(f'{scope}:{window}:{identity}'.encode()).hexdigest()
    if not current_app.extensions['rate_limits'].reserve(key, limit, expires, at):
        raise ApiError(429, 'too_many_attempts', 'Muitas tentativas. Tente novamente mais tarde.',
                       max(1, math.ceil((expires - at).total_seconds())))


def protect_requests():
    # Reject oversized JSON before Flask reads/parses it. Upload retains its own limit.
    if request.path.startswith('/api/'):
        # Only the upload route may exceed the small JSON limit, up to MAX_REQUEST_BYTES.
        request.max_content_length = (current_app.config['MAX_CONTENT_LENGTH']
                                      if request.endpoint == 'videos.create' else 16 * 1024)
        if request.content_length is not None and request.content_length > request.max_content_length:
            raise RequestEntityTooLarge()
    if not request.endpoint or request.endpoint == 'api.health':
        return
    # Players issue many Range requests, so media has its own, larger budget.
    if request.endpoint == 'videos.media':
        consume_budget('media-ip', request.remote_addr or 'unknown', 600, 60)
    else:
        consume_budget('api-ip', request.remote_addr or 'unknown', 120, 60)
    policies = {
        'auth.csrf': ('csrf', 30, 60),
        'auth.register': ('register', 5, 900),
        'auth.login': ('login-ip', 60, 900),
        'api.passages': ('bible', 60, 60),
        'videos.create': ('upload', 10, 900),
        'social.create_comment': ('comment', 30, 60),
        'social.report': ('report', 10, 900),
    }
    policy = policies.get(request.endpoint)
    if policy:
        consume_budget(policy[0], request.remote_addr or 'unknown', policy[1], policy[2])
    consume_budget('public-global', 'all', 1000, 60)
