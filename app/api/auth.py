"""JWT session cookies with CSRF, registration, login/logout and own profile."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from bson import ObjectId
from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.errors import ApiError, EmailConflict, UsernameConflict
from app.core.security import consume_budget


ID_PATTERN = re.compile(r'[0-9a-f]{24}')
# Nome de usuário (@handle): 3–30 caracteres, sem "@", então o login distingue e-mail de usuário pelo "@".
USERNAME_PATTERN = re.compile(r'(?!.*\.\.)[a-z0-9_](?:[a-z0-9._]{1,28}[a-z0-9_])')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def configure_auth(app):
    origin = app.config['APP_ORIGIN']
    parts = urlsplit(origin)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
            or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError('APP_ORIGIN deve ser uma origem HTTP(S), sem caminho.')
    secure = app.config['SESSION_COOKIE_SECURE']
    if not isinstance(secure, bool):
        raise ValueError('SESSION_COOKIE_SECURE deve ser booleano.')
    if not secure and not (app.config['APP_ENV'] == 'testing' or (
            app.config['APP_ENV'] == 'development' and parts.scheme == 'http'
            and parts.hostname in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError('Cookie sem Secure permitido somente em testes ou localhost HTTP.')
    if app.config['ANONYMOUS_SESSION_TTL_SECONDS'] != 1800:
        raise ValueError('Sessão anônima deve durar 1800 segundos.')
    if app.config['SESSION_TTL_SECONDS'] != 43200:
        raise ValueError('Sessão autenticada deve durar 43200 segundos.')
    if not app.config['JWT_SECRET'] and app.config['APP_ENV'] != 'production':
        # Per-process key: restarting the dev server signs everyone out.
        app.config['JWT_SECRET'] = secrets.token_urlsafe(32)
    if len(app.config['JWT_SECRET'].encode('utf-8')) < 32:
        raise ValueError('JWT_SECRET deve ter pelo menos 32 bytes; obrigatório em produção.')


def now():
    clock = current_app.config.get('AUTH_CLOCK')
    return clock() if clock else datetime.now(timezone.utc)


def repository():
    return current_app.extensions['auth_repository']


def check_origin():
    if 'Origin' in request.headers and request.headers['Origin'] != current_app.config['APP_ORIGIN']:
        raise ApiError(403, 'csrf_invalid', 'Origem inválida.')


def issue_token(at, ttl, user_id=None):
    # The random jti makes each token unique; the session record keeps logout revocable.
    claims = {'jti': secrets.token_urlsafe(32), 'iat': int(at.timestamp()),
              'exp': int(at.timestamp()) + ttl}
    if user_id is not None:
        claims['sub'] = str(user_id)
    return jwt.encode(claims, current_app.config['JWT_SECRET'], algorithm='HS256')


def read_session(at):
    token = request.cookies.get('app_session', '')
    if not token or len(token) > 256:
        return None
    try:
        # exp is compared with now() below, so AUTH_CLOCK drives it in tests.
        claims = jwt.decode(token, current_app.config['JWT_SECRET'], algorithms=['HS256'],
                            options={'require': ['jti', 'iat', 'exp'], 'verify_exp': False,
                                     'verify_iat': False})
    except jwt.InvalidTokenError:
        return None
    if claims['exp'] <= at.timestamp():
        return None
    session = repository().find_session(digest(token))
    if session is None or session['expires_at'] <= at:
        return None
    if session.get('user_id'):
        user = repository().find_user_by_id(session['user_id'])
        if not session_matches_user(session, user):
            return None
    return session


def session_matches_user(session, user):
    """Missing versions are legacy generation zero, never the user's current generation."""
    if session is None or user is None:
        return False
    session_version, user_version = session.get('auth_version', 0), user.get('auth_version', 0)
    return (type(session_version) is int and type(user_version) is int
            and session_version >= 0 and session_version == user_version)


def require_csrf():
    check_origin()
    session = read_session(now())
    token = request.headers.get('X-CSRF-Token', '')
    if (session is None or not token or len(token) > 256
            or not hmac.compare_digest(digest(token), session['csrf_hash'])):
        raise ApiError(403, 'csrf_invalid', 'Sessão e token CSRF válidos são obrigatórios.')
    return session


def require_login(session):
    if session is None or not session.get('user_id'):
        raise ApiError(401, 'unauthenticated', 'Autenticação necessária.')
    return session['user_id']


def authenticated_user(session):
    user = repository().find_user_by_id(require_login(session))
    # Recheck the fresh account snapshot: its role may change after read_session().
    if not session_matches_user(session, user):
        raise ApiError(401, 'unauthenticated', 'Autenticação necessária.')
    return user


def csrf_user():
    """A mutating request: valid CSRF first, then the authenticated account."""
    return authenticated_user(require_csrf())


def optional_user():
    """The signed-in account, or None for visitors; never raises for a missing session."""
    session = read_session(now())
    if session is None or not session.get('user_id'):
        return None
    user = repository().find_user_by_id(session['user_id'])
    return user if session_matches_user(session, user) else None


def require_admin(user):
    if user['role'] != 'admin':
        raise ApiError(403, 'forbidden', 'Permissão insuficiente.')
    return user


def json_payload():
    try:
        return request.get_json(silent=False) if request.is_json else None
    except BadRequest:
        raise ApiError(400, 'validation_error', 'JSON inválido.') from None


def check_strings(values):
    for value in values:
        if not isinstance(value, str):
            raise ApiError(400, 'validation_error', 'Dados inválidos.')
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeEncodeError:
            raise ApiError(400, 'validation_error', 'Dados inválidos.') from None


@lru_cache(maxsize=1)
def dummy_hash():
    return generate_password_hash(secrets.token_urlsafe(16), method='scrypt')


def iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def user_public(user):
    return {'id': str(user['_id']), 'name': user['name'], 'username': user.get('username_normalized'),
            'bio': user['bio'], 'created_at': iso(user['created_at'])}


def user_self(user):
    return {**user_public(user), 'email': user['email_normalized'], 'role': user['role']}


def set_session_cookie(response, token, ttl):
    response.set_cookie('app_session', token, max_age=ttl, httponly=True,
                        secure=current_app.config['SESSION_COOKIE_SECURE'],
                        samesite='Lax', path='/')


def validate_user(payload):
    # `username` é opcional para manter o corpo antigo válido; o formulário React sempre envia.
    if not isinstance(payload, dict) or not (
            {'name', 'email', 'password'} <= set(payload) <= {'name', 'username', 'email', 'password'}):
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    name, email, password = (payload[key] for key in ('name', 'email', 'password'))
    username = payload.get('username')
    check_strings((name, email, password, username) if 'username' in payload else (name, email, password))
    if username is not None:
        username = username.strip().lower()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ApiError(400, 'validation_error', 'Dados inválidos.')
    name, email = name.strip(), email.strip().lower()
    local, separator, domain = email.rpartition('@')
    valid_email = (separator and len(local) <= 64 and len(email) <= 254
                   and re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
                   and not local.startswith('.') and not local.endswith('.') and '..' not in local
                   and len(domain.split('.')) >= 2
                   and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in domain.split('.')))
    if not (2 <= len(name) <= 80 and valid_email and 12 <= len(password) <= 128):
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    return name, username, email, password


auth = Blueprint('auth', __name__, url_prefix='/api')


@auth.get('/auth/csrf')
def csrf():
    check_origin()
    at, csrf_token = now(), secrets.token_urlsafe(32)
    session = read_session(at)
    session_token = None
    if session is not None:
        if not repository().rotate_csrf(session['token_hash'], digest(csrf_token), at):
            session = None
    if session is None:
        session_token = issue_token(at, 1800)
        session = {'token_hash': digest(session_token), 'csrf_hash': digest(csrf_token),
                   'created_at': at, 'expires_at': at + timedelta(seconds=1800)}
        repository().insert_session(session)
    response = jsonify(data={'csrf_token': csrf_token})
    if session_token is not None:
        set_session_cookie(response, session_token, 1800)
    return response


@auth.post('/users')
def register():
    require_csrf()
    name, username, email, password = validate_user(json_payload())
    user = {'_id': ObjectId(), 'name': name, 'email_normalized': email,
            'password_hash': generate_password_hash(password, method='scrypt'),
            'bio': '', 'role': 'user', 'auth_version': 0, 'created_at': now()}
    if username is not None:
        user['username_normalized'] = username
    try:
        repository().insert_user(user)
    except EmailConflict:
        raise ApiError(409, 'email_conflict', 'E-mail já cadastrado.') from None
    except UsernameConflict:
        raise ApiError(409, 'username_conflict', 'Nome de usuário já em uso.') from None
    response = jsonify(data=user_self(user))
    response.status_code = 201
    response.headers['Location'] = '/api/users/' + str(user['_id'])
    return response


@auth.post('/auth/login')
def login():
    previous = require_csrf()
    payload = json_payload()
    # `login` aceita e-mail ou nome de usuário; `email` continua valendo pelo contrato antigo.
    if not isinstance(payload, dict) or set(payload) not in ({'email', 'password'}, {'login', 'password'}):
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    identifier, password = payload.get('login', payload.get('email')), payload['password']
    check_strings((identifier, password))
    identifier, at = identifier.strip().lower(), now()
    if len(identifier) > 254 or len(password) > 128:
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    if '@' in identifier:
        user = repository().find_user_by_email(identifier)
    else:
        user = repository().find_user_by_username(identifier) if USERNAME_PATTERN.fullmatch(identifier) else None
    # Reserve before hashing; successful requests also consume their reservation. Keyed by the account
    # when it exists, so alternating e-mail and username does not double the attempts.
    consume_budget('login-pair', f'{request.remote_addr}|{user["_id"] if user else identifier}', 10, 900)
    password_ok = check_password_hash(
        user['password_hash'] if user else dummy_hash(), password)
    if user is None or not password_ok:
        raise ApiError(401, 'invalid_credentials', 'E-mail, usuário ou senha incorretos.')
    ttl = current_app.config['SESSION_TTL_SECONDS']
    session_token, csrf_token = issue_token(at, ttl, user['_id']), secrets.token_urlsafe(32)
    repository().delete_session(previous['token_hash'])
    repository().insert_session({'token_hash': digest(session_token), 'user_id': user['_id'],
                                 'auth_version': user.get('auth_version', 0),
                                 'csrf_hash': digest(csrf_token), 'created_at': at,
                                 'expires_at': at + timedelta(seconds=ttl)})
    response = jsonify(data={'user': user_self(user), 'csrf_token': csrf_token})
    set_session_cookie(response, session_token, ttl)
    return response


@auth.post('/auth/logout')
def logout():
    session = require_csrf()
    require_login(session)
    repository().delete_session(session['token_hash'])
    response = Response(status=204)
    response.delete_cookie('app_session', path='/', httponly=True, samesite='Lax',
                           secure=current_app.config['SESSION_COOKIE_SECURE'])
    return response


@auth.get('/me')
def me():
    return jsonify(data=user_self(authenticated_user(read_session(now()))))


@auth.patch('/me')
def update_me():
    session = require_csrf()
    user = authenticated_user(session)
    payload = json_payload()
    if not isinstance(payload, dict) or not payload or not set(payload) <= {'name', 'bio'}:
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    check_strings(payload.values())
    fields = {key: value.strip() for key, value in payload.items()}
    if ('name' in fields and not 2 <= len(fields['name']) <= 80) or len(fields.get('bio', '')) > 300:
        raise ApiError(400, 'validation_error', 'Dados inválidos.')
    updated = repository().update_user(user['_id'], fields)
    if updated is None:
        raise ApiError(401, 'unauthenticated', 'Autenticação necessária.')
    return jsonify(data=user_self(updated))


@auth.get('/users/<user_id>')
def public_user(user_id):
    if not ID_PATTERN.fullmatch(user_id):
        raise ApiError(400, 'validation_error', 'Identificador inválido.')
    user = repository().find_user_by_id(ObjectId(user_id))
    if user is None:
        raise ApiError(404, 'not_found', 'Usuário não encontrado.')
    return jsonify(data=user_public(user))
