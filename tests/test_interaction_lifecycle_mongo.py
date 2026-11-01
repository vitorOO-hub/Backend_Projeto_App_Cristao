"""Opt-in transaction and maintenance regressions in a new loopback-only database."""
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from flask import Flask
from pymongo import MongoClient

from app.repositories.indexes import ensure_indexes
from app.core.errors import ApiError, DataUnavailable, ReportConflict
from app.services.interactions import MongoInteractions
from app.repositories.orphans import MongoOrphanRepository
from app.services.purge import purge_video
from app.domain.social import new_comment, new_report
from app.repositories.social import MongoCommentRepository, MongoReactionRepository
from app.services.storage import LocalStorage
from app.repositories.transactions import MongoTransactions
from app.repositories.videos import MongoVideoRepository
from app.domain.videos import new_video

AT = datetime(2026, 10, 31, tzinfo=timezone.utc)


@pytest.fixture
def lifecycle_database():
    uri = os.getenv('TEST_MONGODB_URI')
    if not uri:
        pytest.skip('TEST_MONGODB_URI is required for isolated local MongoDB tests.')
    if urlsplit(uri).hostname not in ('localhost', '127.0.0.1', '::1'):
        pytest.fail('The disposable MongoDB test requires a loopback address.')
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                         socketTimeoutMS=10000, tz_aware=True)
    name = 'app_cristao_security_test_' + uuid4().hex
    try:
        assert client.admin.command('hello').get('setName'), 'A replica set is required.'
        database = client[name]
        ensure_indexes(database)
        yield database
    finally:
        client.drop_database(name)
        client.close()


@pytest.fixture
def lifecycle_video(lifecycle_database, tmp_path):
    storage = LocalStorage(tmp_path / 'uploads')
    key = storage.new_key()
    video = new_video(ObjectId(), {'caption': 'Synthetic video', 'category': 'louvor'}, {
        'storage_key': key, 'bytes': 4, 'duration_seconds': 1, 'video_codec': 'h264',
        'audio_codec': None, 'width': 1, 'height': 1}, AT)
    video['status'] = 'approved'
    lifecycle_database.videos.insert_one(video)
    storage.path(key).write_bytes(b'test')
    app = Flask('isolated-lifecycle-test')
    app.extensions.update(storage=storage, video_repository=MongoVideoRepository(lifecycle_database),
                          comment_repository=MongoCommentRepository(lifecycle_database),
                          reaction_repository=MongoReactionRepository(lifecycle_database))
    return video, storage, app


def interact(service, kind, video_id, user_id=None):
    user_id = user_id or ObjectId()
    if kind == 'comment':
        return service.comment(new_comment(video_id, user_id, {'text': 'Synthetic comment'}, AT))
    if kind == 'report':
        return service.report(new_report(video_id, user_id, {'reason': 'Synthetic review reason'}, AT))
    return service.react(user_id, video_id, AT)


@pytest.mark.parametrize('kind', ['comment', 'react', 'report'])
def test_deleted_before_transaction_cannot_receive_interaction(lifecycle_database, lifecycle_video, kind):
    database = lifecycle_database
    video, storage, app = lifecycle_video
    assert app.extensions['video_repository'].soft_delete(video['_id'], video['author_id'], AT)
    with app.app_context():
        purge_video(video)
    with pytest.raises(ApiError) as error:
        interact(MongoInteractions(database), kind, video['_id'])
    assert error.value.status == 404
    assert all(database[name].count_documents({}) == 0 for name in ('comments', 'reactions', 'reports'))
    assert not storage.path(video['file']['storage_key']).exists()


@pytest.mark.parametrize('kind', ['comment', 'react', 'report'])
def test_interaction_before_deletion_is_cleaned_or_retained_for_audit(lifecycle_database, lifecycle_video, kind):
    database = lifecycle_database
    video, storage, app = lifecycle_video
    interact(MongoInteractions(database), kind, video['_id'])
    assert app.extensions['video_repository'].soft_delete(video['_id'], video['author_id'], AT)
    with app.app_context():
        purge_video(video)
    assert database.comments.count_documents({}) == database.reactions.count_documents({}) == 0
    assert database.reports.count_documents({}) == (1 if kind == 'report' else 0)
    assert database.videos.find_one({'_id': video['_id']})['purged_at'] is not None
    assert not storage.path(video['file']['storage_key']).exists()


@pytest.mark.parametrize('kind', ['comment', 'react', 'report'])
def test_deletion_waits_for_the_guarded_interaction_then_cleanup_is_complete(lifecycle_database,
                                                                         lifecycle_video, kind):
    database = lifecycle_database
    video, _, app = lifecycle_video
    locked, release, deleting = Event(), Event(), Event()

    class PausedGuard:
        def update_one(self, *args, **kwargs):
            result = database.videos.update_one(*args, **kwargs)
            locked.set()
            assert release.wait(timeout=10)
            return result

    collections = {name: database[name] for name in ('comments', 'reactions', 'reports')}
    collections['videos'] = PausedGuard()
    service = MongoInteractions(collections, MongoTransactions(database))

    def remove():
        deleting.set()
        return app.extensions['video_repository'].soft_delete(video['_id'], video['author_id'], AT)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(interact, service, kind, video['_id'])
        try:
            assert locked.wait(timeout=10)
            removal = pool.submit(remove)
            assert deleting.wait(timeout=10)
            with pytest.raises(TimeoutError):
                removal.result(timeout=0.1)
        finally:
            release.set()
        writing.result(timeout=10)
        assert removal.result(timeout=10)
    with app.app_context():
        purge_video(video)
    assert database.comments.count_documents({}) == database.reactions.count_documents({}) == 0
    assert database.reports.count_documents({}) == (1 if kind == 'report' else 0)


@pytest.mark.parametrize('kind', ['comment', 'react', 'report'])
def test_stale_snapshot_retries_guard_after_concurrent_deletion(lifecycle_database, lifecycle_video, kind):
    database = lifecycle_database
    video, _, app = lifecycle_video
    snapshot_ready, resume = Event(), Event()
    attempts = []

    class StaleSnapshotGuard:
        def update_one(self, *args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                assert database.videos.find_one({'_id': video['_id']}, session=kwargs['session'])
                snapshot_ready.set()
                assert resume.wait(timeout=10)
            return database.videos.update_one(*args, **kwargs)

    collections = {name: database[name] for name in ('comments', 'reactions', 'reports')}
    collections['videos'] = StaleSnapshotGuard()
    service = MongoInteractions(collections, MongoTransactions(database))
    with ThreadPoolExecutor(max_workers=1) as pool:
        writing = pool.submit(interact, service, kind, video['_id'])
        try:
            assert snapshot_ready.wait(timeout=10)
            assert app.extensions['video_repository'].soft_delete(video['_id'], video['author_id'], AT)
            with app.app_context():
                purge_video(video)
        finally:
            resume.set()
        with pytest.raises(ApiError) as error:
            writing.result(timeout=10)
    assert error.value.status == 404
    assert len(attempts) >= 2
    assert all(database[name].count_documents({}) == 0 for name in ('comments', 'reactions', 'reports'))


def test_duplicate_report_aborts_its_guard_and_reactions_remain_idempotent(lifecycle_database, lifecycle_video):
    database = lifecycle_database
    video, _, _ = lifecycle_video
    service, reporter = MongoInteractions(database), ObjectId()
    interact(service, 'report', video['_id'], reporter)
    version = database.videos.find_one({'_id': video['_id']})['interaction_version']
    with pytest.raises(ReportConflict):
        interact(service, 'report', video['_id'], reporter)
    assert database.videos.find_one({'_id': video['_id']})['interaction_version'] == version
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: service.react(reporter, video['_id'], AT), range(16)))
    assert database.reactions.count_documents({}) == 1
    service.unreact(reporter, video['_id'])
    service.unreact(reporter, video['_id'])
    assert database.reactions.count_documents({}) == 0


def test_failed_insert_rolls_back_the_video_guard(lifecycle_database, lifecycle_video):
    database = lifecycle_database
    video, _, _ = lifecycle_video
    database.command('collMod', 'comments', validator={'text': {'$type': 'int'}})
    with pytest.raises(DataUnavailable):
        interact(MongoInteractions(database), 'comment', video['_id'])
    assert database.comments.count_documents({}) == 0
    assert 'interaction_version' not in database.videos.find_one({'_id': video['_id']})


def test_orphan_query_is_read_only_and_retains_live_hidden_video_relations(lifecycle_database, lifecycle_video):
    database = lifecycle_database
    video, _, app = lifecycle_video
    hidden = {**video, '_id': ObjectId(), 'status': 'rejected'}
    database.videos.insert_one(hidden)
    user = ObjectId()
    for video_id in (video['_id'], hidden['_id'], ObjectId()):
        database.comments.insert_one(new_comment(video_id, user, {'text': 'private text'}, AT))
    assert app.extensions['video_repository'].soft_delete(video['_id'], video['author_id'], AT)
    database.videos.update_one({'_id': video['_id']}, {'$set': {'purged_at': AT}})
    repository = MongoOrphanRepository(database)
    before = list(database.comments.find().sort('_id', 1))
    first, more = repository.list_for('comments', limit=1)
    second, more2 = repository.list_for('comments', limit=1, after=first[-1]['_id'])
    assert len(first) == len(second) == 1 and more and not more2
    assert hidden['_id'] not in {first[0]['video_id'], second[0]['video_id']}
    assert set(first[0]) == {'_id', 'video_id'}
    assert list(database.comments.find().sort('_id', 1)) == before
