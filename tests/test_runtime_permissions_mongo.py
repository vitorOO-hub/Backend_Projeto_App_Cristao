"""Exercise the HTTP workflow with collection-scoped privileges in a disposable local database."""
import io
import os
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from bson import ObjectId

from app import create_app
from app.repositories.auth import MongoAuthRepository
from app.repositories.bible_cache import MongoCache
from app.repositories.indexes import ensure_indexes


RUNTIME_ACTIONS = {
    'users': ['find', 'insert', 'update'],
    'sessions': ['find', 'insert', 'update', 'remove'],
    'rate_limits': ['find', 'insert', 'update'],
    'bible_cache': ['find', 'insert', 'update'],
    'videos': ['find', 'insert', 'update'],
    'reactions': ['find', 'insert', 'update', 'remove'],
    'comments': ['find', 'insert', 'remove'],
    'reports': ['find', 'insert', 'update'],
    'blocks': ['find', 'insert', 'update', 'remove'],
    'follows': ['find', 'insert', 'update', 'remove'],
    'upload_guards': ['find', 'insert', 'update'],
    'upload_reservations': ['find', 'insert', 'update'],
}


@pytest.fixture
def restricted_runtime():
    uri = os.getenv('TEST_MONGODB_URI')
    if not uri:
        pytest.skip('TEST_MONGODB_URI is required for isolated local MongoDB tests.')
    parts = urlsplit(uri)
    if parts.scheme != 'mongodb' or parts.hostname not in ('localhost', '127.0.0.1', '::1'):
        pytest.fail('This test requires a disposable MongoDB on a loopback address.')
    setup = MongoClient(uri, serverSelectionTimeoutMS=3000, socketTimeoutMS=10000)
    name = 'app_cristao_security_test_' + uuid4().hex
    database = setup[name]
    runtime = None
    app = None
    with TemporaryDirectory(prefix='app-cristao-permissions-') as directory:
        try:
            setup.admin.command('ping')
            MongoAuthRepository(database).initialize()
            ensure_indexes(database)
            MongoCache(database.bible_cache).initialize()
            database.rate_limits.create_index('expires_at', expireAfterSeconds=0)
            database.command('createRole', 'runtime_test', privileges=[
                {'resource': {'db': name, 'collection': collection}, 'actions': actions}
                for collection, actions in RUNTIME_ACTIONS.items()
            ], roles=[])
            database.command('createUser', 'runtime_test', pwd='isolated-runtime-test-password',
                             roles=[{'role': 'runtime_test', 'db': name}])
            options = dict(parse_qsl(parts.query))
            options['authSource'] = name
            host = parts.netloc.rsplit('@', 1)[-1]
            runtime_uri = urlunsplit((parts.scheme,
                'runtime_test:isolated-runtime-test-password@' + host, '/', urlencode(options), ''))
            runtime = MongoClient(runtime_uri, serverSelectionTimeoutMS=3000, socketTimeoutMS=10000)
            app = create_app({
                'TESTING': True, 'APP_ENV': 'testing', 'SESSION_COOKIE_SECURE': False,
                'MONGODB_URI': runtime_uri, 'MONGODB_DATABASE': name, 'UPLOAD_DIR': directory,
                'MAX_PENDING_VIDEOS': 1,
                'MEDIA_PROBE': lambda _: {'duration_seconds': 1.0, 'video_codec': 'h264',
                    'audio_codec': None, 'width': 16, 'height': 16},
            })
            yield app, database, runtime[name]
        finally:
            if app is not None:
                app.extensions['mongo'].close()
            if runtime is not None:
                runtime.close()
            # Users/roles are scoped to this newly generated database, never the app database.
            database.command('dropAllUsersFromDatabase')
            database.command('dropAllRolesFromDatabase')
            setup.drop_database(name)
            setup.close()


class SignedIn:
    def __init__(self, app, label):
        self.client = app.test_client()
        self.email = f'{label.lower()}@example.com'
        csrf = self.client.get('/api/auth/csrf')
        assert csrf.status_code == 200, csrf.json
        created = self.client.post('/api/users', json={
            'name': label, 'email': self.email, 'password': 'isolated-password-12345'},
            headers={'X-CSRF-Token': csrf.json['data']['csrf_token']})
        assert created.status_code == 201, created.json
        self.id = created.json['data']['id']
        self.login()

    def login(self):
        csrf = self.client.get('/api/auth/csrf')
        assert csrf.status_code == 200, csrf.json
        result = self.client.post('/api/auth/login', json={
            'email': self.email, 'password': 'isolated-password-12345'},
            headers={'X-CSRF-Token': csrf.json['data']['csrf_token']})
        assert result.status_code == 200, result.json
        self.headers = {'X-CSRF-Token': result.json['data']['csrf_token']}


def test_runtime_role_allows_workflow_and_denies_destructive_administration(restricted_runtime):
    app, setup_database, runtime_database = restricted_runtime
    author, viewer, admin = (SignedIn(app, name) for name in ('Author', 'Viewer', 'Moderator'))
    assert MongoAuthRepository(setup_database).promote_user(admin.email)
    assert admin.client.get('/api/me').status_code == 401
    admin.login()
    upload = author.client.post('/api/videos', headers=author.headers, data={
        'caption': 'Synthetic permission test', 'category': 'louvor',
        'file': (io.BytesIO(b'\x00\x00\x00\x18ftypmp42' + b'0' * 200), 'test.mp4'),
    }, content_type='multipart/form-data')
    assert upload.status_code == 201, upload.json
    video = upload.json['data']
    pending_limit = author.client.post('/api/videos', headers=author.headers, data={
        'caption': 'Must wait for review', 'category': 'louvor',
        'file': (io.BytesIO(b'not-read-before-admission'), 'test.mp4'),
    }, content_type='multipart/form-data')
    assert pending_limit.status_code == 409, pending_limit.json
    assert pending_limit.json['error']['code'] == 'pending_video_limit'
    assert pending_limit.json['error']['message']
    approved = admin.client.patch('/api/moderation/videos/' + video['id'], headers=admin.headers,
        json={'status': 'approved', 'reason': 'Synthetic authorized permission test.',
              'expected_updated_at': video['updated_at']})
    assert approved.status_code == 200, approved.json
    # Synthetic legacy metadata tests account quotas without allocating a GiB on disk.
    setup_database.videos.update_one({'_id': ObjectId(video['id'])},
        {'$set': {'file.bytes': app.config['MAX_USER_STORAGE_BYTES']}})
    quota_limit = author.client.post('/api/videos', headers=author.headers, data={
        'caption': 'Must release account space', 'category': 'louvor',
        'file': (io.BytesIO(b'not-read-before-admission'), 'test.mp4'),
    }, content_type='multipart/form-data')
    assert quota_limit.status_code == 409, quota_limit.json
    assert quota_limit.json['error']['code'] == 'storage_quota_exceeded'
    assert quota_limit.json['error']['message']
    reaction = viewer.client.put(f'/api/videos/{video["id"]}/reaction', headers=viewer.headers)
    assert reaction.status_code == 200, reaction.json
    comment = viewer.client.post(f'/api/videos/{video["id"]}/comments', headers=viewer.headers,
                                 json={'text': 'Synthetic comment'})
    assert comment.status_code == 201, comment.json
    report = viewer.client.post(f'/api/videos/{video["id"]}/reports', headers=viewer.headers,
                                json={'reason': 'Synthetic report for permission test.'})
    assert report.status_code == 201, report.json
    followed = viewer.client.put(f'/api/users/{author.id}/follow', headers=viewer.headers)
    assert followed.status_code == 200, followed.json
    profile = viewer.client.get('/api/me/profile')
    assert profile.status_code == 200 and profile.json['data']['counts']['following'] == 1, profile.json
    blocked = viewer.client.put(f'/api/me/blocks/{author.id}', headers=viewer.headers)
    assert blocked.status_code == 200, blocked.json
    assert viewer.client.get(f'/api/videos/{video["id"]}').status_code == 404
    feed = viewer.client.get('/api/videos')
    assert feed.status_code == 200 and feed.json['data'] == [], feed.json
    following = viewer.client.get('/api/me/following')
    assert following.status_code == 200 and following.json['data'] == [], following.json
    comments = author.client.get(f'/api/videos/{video["id"]}/comments')
    assert comments.status_code == 200 and len(comments.json['data']) == 1, comments.json
    assert viewer.client.delete(f'/api/me/blocks/{author.id}', headers=viewer.headers).status_code == 204
    deleted = author.client.delete('/api/videos/' + video['id'], headers=author.headers)
    assert deleted.status_code == 204, deleted.json
    assert setup_database.reactions.count_documents({}) == 0
    assert setup_database.comments.count_documents({}) == 0
    assert setup_database.reports.count_documents({}) == 1
    removed = setup_database.videos.find_one({'_id': ObjectId(video['id'])})
    assert removed['file_removed_at'] is not None
    assert not app.extensions['storage'].path(removed['file']['storage_key']).exists()
    replacement = author.client.post('/api/videos', headers=author.headers, data={
        'caption': 'Space released after physical deletion', 'category': 'louvor',
        'file': (io.BytesIO(b'\x00\x00\x00\x18ftypmp42' + b'0' * 200), 'test.mp4'),
    }, content_type='multipart/form-data')
    assert replacement.status_code == 201, replacement.json
    with pytest.raises(OperationFailure) as denied_drop:
        runtime_database.client.drop_database(runtime_database.name)
    assert denied_drop.value.code == 13
    with pytest.raises(OperationFailure) as denied_remove:
        runtime_database.users.delete_one({'email_normalized': author.email})
    assert denied_remove.value.code == 13
