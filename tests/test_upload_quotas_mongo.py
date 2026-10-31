"""Opt-in shared quota ledger and process-crash recovery against disposable local MongoDB."""
import json
import os
import subprocess
import sys
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from bson import ObjectId
from flask import Flask

from app.errors import ApiError, DataUnavailable, StorageUnavailable
from app.storage import LocalStorage
from app.upload_files import LeaseBusy, UploadLease
from app.uploads import ACTIVE, MongoUploadRepository, UploadManager, UploadPolicy
from app.videos import new_video
from test_interaction_lifecycle_mongo import AT, lifecycle_database

Disk = namedtuple('Disk', 'total used free')


@pytest.fixture
def mongo_uploads(lifecycle_database, tmp_path):
    storage = LocalStorage(tmp_path / 'uploads')
    config = {'MAX_USER_STORAGE_BYTES': 100, 'MAX_PENDING_VIDEOS': 2,
              'MAX_STORAGE_USED_PERCENT': 80, 'MIN_STORAGE_FREE_BYTES': 0,
              'UPLOAD_VOLUME_ID': 'test-media', 'MAX_VIDEO_BYTES': 10, 'MAX_CONTENT_LENGTH': 20}
    repository = MongoUploadRepository(lifecycle_database)
    manager = UploadManager(storage, repository, config, lambda: Disk(10000, 1000, 9000))
    app = Flask('isolated-upload-test')
    app.config['AUTH_CLOCK'] = lambda: AT
    return app, lifecycle_database, manager, config


def video_for(attempt, size=4):
    return new_video(attempt.metadata['author_id'], {'caption': 'Synthetic upload', 'category': 'louvor'}, {
        'storage_key': attempt.key, 'bytes': size, 'duration_seconds': 1, 'video_codec': 'h264',
        'audio_codec': None, 'width': 1, 'height': 1}, AT)


def staged_file(attempt, contents=b'test'):
    path = attempt.lease.directory / 'copy.part'
    path.write_bytes(contents)
    return path


@pytest.mark.parametrize('limiter', ['pending', 'bytes'])
def test_concurrent_mongo_admission_never_exceeds_account_limits(mongo_uploads, limiter):
    app, database, manager, config = mongo_uploads
    if limiter == 'bytes':
        config.update(MAX_USER_STORAGE_BYTES=20, MAX_PENDING_VIDEOS=100)
        manager.policy = UploadPolicy.from_config(config)
    account = ObjectId()

    def reserve(_):
        with app.app_context():
            try:
                return manager.begin(account)
            except ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    attempts = [result for result in results if not isinstance(result, str)]
    try:
        assert len(attempts) == 2
        assert results.count('pending_video_limit' if limiter == 'pending' else 'storage_quota_exceeded') == 6
        assert database.upload_reservations.count_documents({'state': {'$in': list(ACTIVE)}}) == 2
    finally:
        with app.app_context():
            for attempt in attempts:
                attempt.abort()
                attempt.close()


def test_concurrent_mongo_volume_reservation_is_shared_across_accounts(mongo_uploads):
    app, database, manager, _ = mongo_uploads
    manager.capacity_probe = lambda: Disk(1000, 700, 300)

    def reserve(_):
        with app.app_context():
            try:
                return manager.begin(ObjectId())
            except StorageUnavailable:
                return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(filter(None, pool.map(reserve, range(8))))
    try:
        assert len(attempts) == 2
        rows = list(database.upload_reservations.find({'state': 'reserved'}))
        assert sum(row['volume_bytes'] for row in rows) == 80
    finally:
        with app.app_context():
            for attempt in attempts:
                attempt.abort()
                attempt.close()


def test_mongo_account_quota_includes_other_states_and_deleted_unremoved_files(mongo_uploads):
    app, database, manager, config = mongo_uploads
    account = ObjectId()
    with app.app_context():
        first = manager.begin(account)
        video = video_for(first, size=91)
        # A pre-existing legacy file may be larger than today's admission reservation.
        database.videos.insert_one({**video, 'status': 'rejected', 'deleted_at': AT})
        first.abort()
        with pytest.raises(ApiError) as error:
            manager.begin(account)
        assert error.value.code == 'storage_quota_exceeded'
        assert database.videos.count_documents({}) == 1
        manager.file_removed(video)
        manager.file_removed(video)
        assert database.videos.find_one({'_id': video['_id']})['file_removed_at'] == AT
        admitted = manager.begin(account)
        admitted.abort()


@pytest.mark.parametrize('committed', [False, True])
def test_mongo_ambiguous_finalize_preserves_file_until_reconciliation(mongo_uploads, committed, monkeypatch):
    app, database, manager, _ = mongo_uploads
    complete = manager.repository.complete

    def ambiguous(metadata, video):
        if committed:
            complete(metadata, video)
        raise DataUnavailable()

    monkeypatch.setattr(manager.repository, 'complete', ambiguous)
    with app.app_context():
        attempt = manager.begin(ObjectId())
        video = video_for(attempt)
        with pytest.raises(DataUnavailable):
            attempt.publish(staged_file(attempt), video)
        attempt.close()
        assert manager.storage.path(attempt.key).exists()
        state = database.upload_reservations.find_one({'_id': attempt.metadata['_id']})['state']
        assert state == ('committed' if committed else 'reserved')
        rows, _ = manager.reconcile()
        assert rows[0][1] == state
        assert manager.storage.path(attempt.key).exists()
        manager.reconcile(apply=True)
    assert manager.storage.path(attempt.key).exists() is committed
    assert database.videos.count_documents({}) == (1 if committed else 0)


def test_mongo_recovery_fence_prevents_late_finalizer(mongo_uploads):
    app, database, manager, _ = mongo_uploads
    with app.app_context():
        attempt = manager.begin(ObjectId())
        manager.storage.commit(staged_file(attempt), attempt.key)
        attempt.close()
        assert manager.repository.prepare_recovery(attempt.metadata) == 'discard'
        with pytest.raises(StorageUnavailable):
            manager.repository.complete(attempt.metadata, video_for(attempt))
        assert database.videos.count_documents({}) == 0
        manager.reconcile(apply=True)
        assert database.upload_reservations.find_one({'_id': attempt.metadata['_id']})['state'] == 'cancelled'
        with pytest.raises(DataUnavailable):
            manager.repository.reserve(attempt.metadata, manager.policy, 10, 40,
                                       lambda: Disk(10000, 1000, 9000))
        assert database.upload_reservations.find_one({'_id': attempt.metadata['_id']})['state'] == 'cancelled'
    assert not manager.storage.path(attempt.key).exists()


def test_real_process_crash_keeps_quota_until_explicit_recovery(mongo_uploads):
    app, database, manager, config = mongo_uploads
    account = ObjectId()
    config['MAX_PENDING_VIDEOS'] = 1
    manager.policy = UploadPolicy.from_config(config)
    settings = {**config, 'MONGODB_DATABASE': database.name,
                'UPLOAD_DIR': str(manager.storage.root), 'APP_ENV': 'testing', 'TESTING': True}
    source = '''
import json, os
from bson import ObjectId
from collections import namedtuple
from app import create_app
settings = json.loads(os.environ['UPLOAD_TEST_SETTINGS'])
settings['MONGODB_URI'] = os.environ['TEST_MONGODB_URI']
Disk = namedtuple('Disk', 'total used free')
settings['UPLOAD_CAPACITY_PROBE'] = lambda: Disk(10000, 1000, 9000)
app = create_app(settings)
with app.app_context():
    attempt = app.extensions['upload_manager'].begin(ObjectId(os.environ['UPLOAD_TEST_ACCOUNT']))
    stream = attempt.lease.spool()
    stream.write(b'partial')
    stream.flush()
    print(attempt.metadata['_id'], flush=True)
    os._exit(23)
'''
    env = {**os.environ, 'UPLOAD_TEST_SETTINGS': json.dumps(settings), 'UPLOAD_TEST_ACCOUNT': str(account)}
    result = subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 23, result.stderr
    identity = result.stdout.strip()
    record = database.upload_reservations.find_one({'_id': identity})
    assert record is not None and record['state'] == 'reserved'
    folder = manager.storage.root / '.upload-leases' / identity
    assert list(folder.glob('*.part'))
    with app.app_context():
        with pytest.raises(ApiError) as error:
            manager.begin(account)
        assert error.value.code == 'pending_video_limit'
        before = database.upload_reservations.find_one({'_id': identity})
        manager.reconcile()
        assert database.upload_reservations.find_one({'_id': identity}) == before
        assert list(folder.glob('*.part'))
        rows, _ = manager.reconcile(apply=True)
        assert (identity, 'cancelled') in rows
        assert database.upload_reservations.find_one({'_id': identity})['state'] == 'cancelled'
        assert not folder.exists()
        attempt = manager.begin(account)
        attempt.abort()


def test_mongo_terminal_cleanup_does_not_let_concurrent_reconcile_revive_quota(mongo_uploads, monkeypatch):
    app, database, manager, config = mongo_uploads
    config['MAX_PENDING_VIDEOS'] = 1
    manager.policy = UploadPolicy.from_config(config)
    account = ObjectId()
    with app.app_context():
        attempt = manager.begin(account)
        folder = attempt.lease.directory
        original_close, original_unlink = attempt.lease.close, Path.unlink
        recovered, events = [], []

        def check_unlink(path, *args, **kwargs):
            if path.parent == folder and path.name == 'manifest.json':
                assert attempt.lease.lock is not None
                with pytest.raises(LeaseBusy):
                    UploadLease(manager.storage.root, attempt.metadata['_id'])
                events.append('manifest-locked')
            if path.parent == folder and path.name == 'lease.lock':
                assert attempt.lease.lock is None
                events.append('lock-closed')
            return original_unlink(path, *args, **kwargs)

        def interleave_close():
            original_close()
            if not recovered:
                assert not (folder / 'manifest.json').exists()
                recovered.extend(manager.reconcile(apply=True)[0])

        monkeypatch.setattr(Path, 'unlink', check_unlink)
        monkeypatch.setattr(attempt.lease, 'close', interleave_close)
        attempt.abort()
        assert recovered == [(attempt.metadata['_id'], 'unavailable')]
        assert events == ['manifest-locked', 'lock-closed']
        assert database.upload_reservations.find_one({'_id': attempt.metadata['_id']})['state'] == 'cancelled'
        assert database.upload_reservations.count_documents({'state': {'$in': list(ACTIVE)}}) == 0
        assert not folder.exists()
        admitted = manager.begin(account)
        admitted.abort()


def test_mongo_cancelled_recovery_stays_terminal_after_partial_cleanup_failure(mongo_uploads, monkeypatch):
    app, database, manager, config = mongo_uploads
    config['MAX_PENDING_VIDEOS'] = 1
    manager.policy = UploadPolicy.from_config(config)
    account = ObjectId()
    with app.app_context():
        attempt = manager.begin(account)
        (attempt.lease.directory / 'leftover.part').write_bytes(b'partial')
        manager.repository.cancel(attempt.metadata)
        attempt.close()
        before = database.upload_reservations.find_one({'_id': attempt.metadata['_id']})
        original_discard = UploadLease.discard_payload

        def fail_old_workspace(lease):
            if lease.id == attempt.metadata['_id']:
                raise StorageUnavailable()
            return original_discard(lease)

        monkeypatch.setattr(UploadLease, 'discard_payload', fail_old_workspace)
        assert manager.reconcile(apply=True)[0] == [(attempt.metadata['_id'], 'unavailable')]
        assert database.upload_reservations.find_one({'_id': attempt.metadata['_id']}) == before
        assert before['state'] == 'cancelled'
        admitted = manager.begin(account)
        admitted.abort()
        monkeypatch.setattr(UploadLease, 'discard_payload', original_discard)
        assert manager.reconcile(apply=True)[0] == [(attempt.metadata['_id'], 'cancelled')]
        assert not attempt.lease.directory.exists()


def test_mongo_cancelled_marker_cannot_delete_confirmed_media(mongo_uploads):
    app, database, manager, _ = mongo_uploads
    with app.app_context():
        attempt = manager.begin(ObjectId())
        video = video_for(attempt)
        manager.storage.commit(staged_file(attempt), attempt.key)
        manager.repository.complete(attempt.metadata, video)
        # Preserve media even when a terminal marker and a confirmed video disagree.
        database.upload_reservations.update_one({'_id': attempt.metadata['_id']}, {'$set': {'state': 'cancelled'}})
        attempt.close()
        assert manager.reconcile(apply=True)[0] == [(attempt.metadata['_id'], 'kept')]
        assert database.upload_reservations.find_one({'_id': attempt.metadata['_id']})['state'] == 'cancelled'
        assert database.videos.find_one({'_id': video['_id']}) is not None
        assert manager.storage.path(attempt.key).exists()
