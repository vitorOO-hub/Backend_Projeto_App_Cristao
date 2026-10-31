"""A complete app wired to in-memory repositories, with signed-in actors that skip scrypt."""
import io
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app import create_app
from app.auth import digest, issue_token
from app.storage import LocalStorage
from app.videos import new_video
from conftest import FakeAuthRepository
from fakes import (FakeBlockRepository, FakeCommentRepository, FakeFollowRepository, FakeInteractions,
                   FakeOrphanRepository,
                   FakeReactionRepository, FakeReportRepository, FakeVideoRepository)

MP4_BYTES = b'\x00\x00\x00\x18ftypmp42' + b'0' * 200
PROBE_RESULT = {'duration_seconds': 42.5, 'video_codec': 'h264', 'audio_codec': 'aac',
                'width': 1080, 'height': 1920}


class Actor:
    """A client with its own cookie and CSRF token (or none, for a visitor)."""

    def __init__(self, app, user=None, token=None, csrf=None):
        self.client = app.test_client()
        self.user = user
        self.id = user['_id'] if user else None
        self.headers = {}
        if token:
            self.client.set_cookie('app_session', token)
            self.headers = {'X-CSRF-Token': csrf}

    def send(self, method, path, headers=None, **kwargs):
        return getattr(self.client, method)(path, headers={**self.headers, **(headers or {})}, **kwargs)

    def get(self, path, **kwargs):
        return self.send('get', path, **kwargs)

    def post(self, path, **kwargs):
        return self.send('post', path, **kwargs)

    def put(self, path, **kwargs):
        return self.send('put', path, **kwargs)

    def patch(self, path, **kwargs):
        return self.send('patch', path, **kwargs)

    def delete(self, path, **kwargs):
        return self.send('delete', path, **kwargs)

    def upload(self, content=MP4_BYTES, filename='clip.mp4', **fields):
        data = {'caption': 'Louvor da manhã', 'category': 'louvor', **fields}
        if content is not None:
            data['file'] = (io.BytesIO(content), filename)
        return self.post('/api/videos', data=data, content_type='multipart/form-data')


class World:
    def __init__(self, tmp_path):
        self.auth = FakeAuthRepository()
        self.videos = FakeVideoRepository()
        self.reactions = FakeReactionRepository()
        self.comments = FakeCommentRepository()
        self.reports = FakeReportRepository()
        self.blocks = FakeBlockRepository()
        self.follows = FakeFollowRepository()
        self.reactions.videos = self.videos
        self.interactions = FakeInteractions(self.videos, self.reactions, self.comments, self.reports)
        self.orphans = FakeOrphanRepository(self.videos, self.reactions, self.comments)
        self.storage = LocalStorage(tmp_path / 'uploads')
        self.time = [datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)]
        self.probe_calls, self.probe_result, self.probe_error = [], dict(PROBE_RESULT), None
        self.app = create_app({
            'TESTING': True, 'APP_ENV': 'testing', 'MONGODB_URI': '', 'SESSION_COOKIE_SECURE': False,
            'CACHE_SQLITE_PATH': tmp_path / 'cache', 'AUTH_CLOCK': lambda: self.time[0],
            'AUTH_REPOSITORY': self.auth, 'VIDEO_REPOSITORY': self.videos,
            'REACTION_REPOSITORY': self.reactions, 'COMMENT_REPOSITORY': self.comments,
            'REPORT_REPOSITORY': self.reports, 'BLOCK_REPOSITORY': self.blocks, 'FOLLOW_REPOSITORY': self.follows,
            'INTERACTIONS_SERVICE': self.interactions, 'ORPHAN_REPOSITORY': self.orphans,
            'STORAGE': self.storage, 'MEDIA_PROBE': self.probe})
        self.serial = 0

    def probe(self, path):
        self.probe_calls.append(path)
        if self.probe_error:
            raise self.probe_error
        return dict(self.probe_result)

    def advance(self, **delta):
        self.time[0] += timedelta(**delta)

    def visitor(self):
        return Actor(self.app)

    def anonymous_session(self):
        """A visitor holding a valid CSRF token but no account."""
        actor = Actor(self.app)
        actor.headers = {'X-CSRF-Token': actor.client.get('/api/auth/csrf').json['data']['csrf_token']}
        return actor

    def user(self, name='Maria', role='user'):
        self.serial += 1
        email = f'{name.lower()}{self.serial}@example.com'
        user = {'_id': ObjectId(), 'name': name, 'email_normalized': email, 'password_hash': 'unused',
                'bio': '', 'role': role, 'created_at': self.time[0]}
        self.auth.users[email] = user
        csrf = f'csrf-{self.serial}-{ObjectId()}'
        with self.app.app_context():
            token = issue_token(self.time[0], 43200, user['_id'])
        self.auth.sessions[digest(token)] = {
            'token_hash': digest(token), 'user_id': user['_id'], 'csrf_hash': digest(csrf),
            'created_at': self.time[0], 'expires_at': self.time[0] + timedelta(hours=12)}
        return Actor(self.app, user, token, csrf)

    def admin(self, name='Admin'):
        return self.user(name, role='admin')

    def video(self, author, status='approved', category='louvor', caption=None, with_file=True):
        """Insert a video straight into the repositories, with a real file on disk."""
        self.serial += 1
        key = self.storage.new_key()
        info = {'storage_key': key, 'bytes': len(MP4_BYTES), **PROBE_RESULT}
        at = self.time[0] + timedelta(seconds=self.serial)
        video = new_video(author.id, {'caption': caption or f'Vídeo {self.serial}', 'category': category}, info, at)
        video['status'] = status
        if status != 'pending':
            video['review'] = {'reason': 'Conteúdo adequado ao público.', 'decided_by': ObjectId(),
                               'decided_at': at}
        if with_file:
            self.storage.path(key).write_bytes(MP4_BYTES)
        self.videos.insert(video)
        return video
