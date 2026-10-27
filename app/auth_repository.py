"""Mongo persistence for accounts and opaque sessions; no local fallback."""
from typing import Protocol

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.errors import AuthUnavailable, EmailConflict, UsernameConflict


def scalar_selector(value):
    if not isinstance(value, (str, ObjectId)):
        raise ValueError('Seletor deve ser escalar, sem operadores fornecidos pelo cliente.')
    return value


class AuthRepository(Protocol):
    def initialize(self): ...
    def find_session(self, token_hash): ...
    def insert_session(self, document): ...
    def rotate_csrf(self, token_hash, csrf_hash, now): ...
    def insert_user(self, document): ...
    def find_user_by_email(self, email): ...
    def find_user_by_username(self, username): ...
    def find_user_by_id(self, user_id): ...
    def find_users_by_ids(self, user_ids): ...
    def promote_user(self, email): ...
    def update_user(self, user_id, fields): ...
    def set_role(self, user_id, role): ...
    def delete_session(self, token_hash): ...
    def failed_logins(self, key, since): ...
    def record_failed_login(self, key, at, expires_at): ...
    def clear_failed_logins(self, key): ...


class UnavailableAuthRepository:
    def _unavailable(self, *args):
        raise AuthUnavailable()

    initialize = find_session = insert_session = rotate_csrf = insert_user = _unavailable
    find_user_by_email = find_user_by_username = find_user_by_id = update_user = set_role = delete_session = _unavailable
    find_users_by_ids = promote_user = _unavailable
    failed_logins = record_failed_login = clear_failed_logins = _unavailable


class MongoAuthRepository:
    def __init__(self, database):
        self.users = database['users']
        self.sessions = database['sessions']
        self.attempts = database['login_attempts']

    def initialize(self):
        try:
            self.users.create_index('email_normalized', unique=True, name='users_email_unique')
            # Parcial: contas criadas antes do nome de usuário não têm o campo e não colidem entre si.
            self.users.create_index('username_normalized', unique=True, name='users_username_unique',
                                    partialFilterExpression={'username_normalized': {'$type': 'string'}})
            self.sessions.create_index('token_hash', unique=True, name='sessions_token_unique')
            self.sessions.create_index('expires_at', expireAfterSeconds=0, name='sessions_expiry')
            self.attempts.create_index([('key_hash', 1), ('created_at', 1)], name='login_attempts_key')
            self.attempts.create_index('expires_at', expireAfterSeconds=0, name='login_attempts_expiry')
        except PyMongoError:
            raise AuthUnavailable() from None

    def find_session(self, token_hash):
        scalar_selector(token_hash)
        try:
            return self.sessions.find_one({'token_hash': token_hash})
        except PyMongoError:
            raise AuthUnavailable() from None

    def insert_session(self, document):
        try:
            self.sessions.insert_one(document)
        except PyMongoError:
            raise AuthUnavailable() from None

    def rotate_csrf(self, token_hash, csrf_hash, now):
        scalar_selector(token_hash)
        try:
            result = self.sessions.update_one(
                {'token_hash': token_hash, 'expires_at': {'$gt': now}},
                {'$set': {'csrf_hash': csrf_hash}})
            return result.matched_count == 1
        except PyMongoError:
            raise AuthUnavailable() from None

    def insert_user(self, document):
        try:
            self.users.insert_one(document)
        except DuplicateKeyError as error:
            if 'username_normalized' in ((error.details or {}).get('keyPattern') or {}):
                raise UsernameConflict() from None
            raise EmailConflict() from None
        except PyMongoError:
            raise AuthUnavailable() from None
        return document

    def find_user_by_email(self, email):
        scalar_selector(email)
        try:
            return self.users.find_one({'email_normalized': email})
        except PyMongoError:
            raise AuthUnavailable() from None

    def find_user_by_username(self, username):
        scalar_selector(username)
        try:
            return self.users.find_one({'username_normalized': username})
        except PyMongoError:
            raise AuthUnavailable() from None

    def find_user_by_id(self, user_id):
        scalar_selector(user_id)
        try:
            return self.users.find_one({'_id': user_id})
        except PyMongoError:
            raise AuthUnavailable() from None

    def find_users_by_ids(self, user_ids):
        """Authors of one page of results, keyed by id."""
        user_ids = list(user_ids)
        if len(user_ids) > 100 or any(not isinstance(value, ObjectId) for value in user_ids):
            raise ValueError('Lote de identificadores inválido.')
        if not user_ids:
            return {}
        try:
            return {user['_id']: user for user in self.users.find({'_id': {'$in': user_ids}})}
        except PyMongoError:
            raise AuthUnavailable() from None

    def promote_user(self, email):
        """Operational use only (flask create-admin); there is no HTTP route for this."""
        scalar_selector(email)
        try:
            return self.users.update_one({'email_normalized': email},
                                         {'$set': {'role': 'admin'}}).matched_count == 1
        except PyMongoError:
            raise AuthUnavailable() from None

    def update_user(self, user_id, fields):
        scalar_selector(user_id)
        if (not isinstance(fields, dict) or not fields or not set(fields) <= {'name', 'bio'}
                or any(not isinstance(value, str) for value in fields.values())):
            raise ValueError('Atualização de perfil aceita somente nome/bio textuais.')
        try:
            return self.users.find_one_and_update(
                {'_id': user_id}, {'$set': fields}, return_document=ReturnDocument.AFTER)
        except PyMongoError:
            raise AuthUnavailable() from None

    def set_role(self, user_id, role):
        scalar_selector(user_id)
        if role not in ('user', 'admin'):
            raise ValueError('Papel deve ser user ou admin.')
        try:
            self.users.update_one({'_id': user_id}, {'$set': {'role': role}})
            # Sessões abertas não herdam o papel novo: promoção ou rebaixamento exige novo login.
            self.sessions.delete_many({'user_id': user_id})
        except PyMongoError:
            raise AuthUnavailable() from None

    def delete_session(self, token_hash):
        scalar_selector(token_hash)
        try:
            self.sessions.delete_one({'token_hash': token_hash})
        except PyMongoError:
            raise AuthUnavailable() from None

    def failed_logins(self, key, since):
        scalar_selector(key)
        try:
            cursor = self.attempts.find({'key_hash': key, 'created_at': {'$gt': since}})
            return [item['created_at'] for item in cursor.sort('created_at', 1)]
        except PyMongoError:
            raise AuthUnavailable() from None

    def record_failed_login(self, key, at, expires_at):
        scalar_selector(key)
        try:
            self.attempts.insert_one({'key_hash': key, 'created_at': at, 'expires_at': expires_at})
        except PyMongoError:
            raise AuthUnavailable() from None

    def clear_failed_logins(self, key):
        scalar_selector(key)
        try:
            self.attempts.delete_many({'key_hash': key})
        except PyMongoError:
            raise AuthUnavailable() from None
