"""Upload admission, physical accounting and recovery without external data."""
import io
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from app import create_app
from app.errors import ApiError, StorageUnavailable
from app.purge import purge_pending
from app.upload_files import LeaseBusy, UploadLease
from app.uploads import ACTIVE, UploadPolicy
from app.videos import new_video
from world import MP4_BYTES, PROBE_RESULT

Disk = namedtuple('Disk', 'total used free')


def configure(world, **settings):
    world.app.config.update(settings)
    world.app.extensions['upload_manager'].policy = UploadPolicy.from_config(world.app.config)
    return world.app.extensions['upload_manager']


class BodySpy(io.BytesIO):
    reads = 0

    def read(self, *args):
        self.reads += 1
        return super().read(*args)

    def readinto(self, *args):
        self.reads += 1
        return super().readinto(*args)


def test_pending_limit_rejects_before_parsing_or_reading_request_body(world):
    actor = world.user()
    configure(world, MAX_PENDING_VIDEOS=1)
    world.video(actor, status='pending')
    body = BodySpy(b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="v.mp4"\r\n\r\ntest\r\n--boundary--\r\n')
    response = actor.client.open('/api/videos', method='POST', input_stream=body,
                                 content_length=len(body.getvalue()),
                                 content_type='multipart/form-data; boundary=boundary', headers=actor.headers)
    assert response.status_code == 409 and response.json['error']['code'] == 'pending_video_limit'
    assert body.reads == 0 and not world.probe_calls
    assert not list((world.storage.root / '.upload-leases').iterdir())


@pytest.mark.parametrize('status,deleted', [('approved', False), ('rejected', False),
                                           ('pending', False), ('approved', True)])
def test_quota_counts_every_state_and_deleted_files_until_physical_removal(world, status, deleted):
    actor = world.user()
    configure(world, MAX_USER_STORAGE_BYTES=len(MP4_BYTES), MAX_VIDEO_BYTES=len(MP4_BYTES))
    video = world.video(actor, status=status)
    if deleted:
        world.videos.soft_delete(video['_id'], actor.id, world.time[0])
    before = deepcopy(world.videos.videos)
    response = actor.upload()
    assert response.status_code == 409 and response.json['error']['code'] == 'storage_quota_exceeded'
    assert world.videos.videos == before
    assert world.storage.path(video['file']['storage_key']).exists()


def test_pending_limit_does_not_prevent_metadata_edit_or_deletion(world):
    actor = world.user()
    configure(world, MAX_PENDING_VIDEOS=1)
    first, approved = world.video(actor, status='pending'), world.video(actor)
    response = actor.patch(f'/api/videos/{approved["_id"]}', json={'caption': 'Edited caption'})
    assert response.status_code == 200 and response.json['data']['status'] == 'pending'
    assert actor.upload().json['error']['code'] == 'pending_video_limit'
    assert actor.delete(f'/api/videos/{first["_id"]}').status_code == 204
    assert actor.delete(f'/api/videos/{approved["_id"]}').status_code == 204
    assert actor.upload().status_code == 201


def test_concurrent_account_admission_reserves_slots_before_work_begins(world):
    actor = world.user()
    manager = configure(world, MAX_PENDING_VIDEOS=2)

    def reserve(_):
        with world.app.app_context():
            try:
                return manager.begin(actor.id)
            except ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(8)))
    attempts = [item for item in outcomes if not isinstance(item, str)]
    try:
        assert len(attempts) == 2
        assert outcomes.count('pending_video_limit') == 6
        assert sum(r['state'] in ACTIVE for r in world.uploads.reservations.values()) == 2
    finally:
        with world.app.app_context():
            for attempt in attempts:
                attempt.abort()
                attempt.close()


def test_concurrent_volume_admission_counts_other_accounts_and_all_spool_copies(world):
    manager = configure(world, MAX_VIDEO_BYTES=10, MAX_CONTENT_LENGTH=20,
                        MAX_USER_STORAGE_BYTES=100, MIN_STORAGE_FREE_BYTES=0)
    manager.capacity_probe = lambda: Disk(1000, 700, 300)
    actors = [world.user() for _ in range(8)]

    def reserve(actor):
        with world.app.app_context():
            try:
                return manager.begin(actor.id)
            except StorageUnavailable:
                return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(filter(None, pool.map(reserve, actors)))
    try:
        assert len(attempts) == 2  # two 40-byte double-request reservations fit below 80%
    finally:
        with world.app.app_context():
            for attempt in attempts:
                attempt.abort()
                attempt.close()


@pytest.mark.parametrize('disk', [Disk(1000000, 781000, 219000), Disk(1000000, 600000, 200000)])
def test_volume_thresholds_return_clear_503_before_receiving_file(world, disk):
    manager = configure(world, MAX_VIDEO_BYTES=10, MAX_CONTENT_LENGTH=10000,
                        MIN_STORAGE_FREE_BYTES=181000)
    manager.capacity_probe = lambda: disk
    response = world.user().upload()
    assert response.status_code == 503 and response.json['error']['code'] == 'storage_unavailable'
    assert response.headers['Retry-After'] == '60'
    assert not world.probe_calls


def test_parser_spools_and_copy_live_on_reserved_volume_and_are_cleaned(world, monkeypatch):
    receive = world.storage.receive
    observed = []

    def inspect(stream, size, *, temp_dir=None):
        assert Path(stream.name).parent == Path(temp_dir)
        assert Path(temp_dir).parent == world.storage.root / '.upload-leases'
        metadata = next(r for r in world.uploads.reservations.values() if r['state'] == 'reserved')
        with pytest.raises(LeaseBusy):
            UploadLease(world.storage.root, metadata['_id'])
        observed.append(1)
        return receive(stream, size, temp_dir=temp_dir)

    monkeypatch.setattr(world.storage, 'receive', inspect)
    assert world.user().upload().status_code == 201
    assert observed == [1]
    assert not list((world.storage.root / '.upload-leases').iterdir())


@pytest.mark.parametrize('committed', [False, True])
def test_unknown_commit_result_preserves_file_until_fenced_recovery(world, committed):
    world.uploads.complete_error_after = committed
    world.uploads.complete_error_before = not committed
    assert world.user().upload().status_code == 503
    record = next(iter(world.uploads.reservations.values()))
    path = world.storage.path(record['storage_key'])
    assert path.exists()
    runner = world.app.test_cli_runner()
    before = deepcopy(world.uploads.reservations)
    diagnosis = runner.invoke(args=['reconcile-uploads'])
    assert diagnosis.exit_code == 0 and 'Somente diagnóstico' in diagnosis.output
    assert path.exists() and world.uploads.reservations == before
    result = runner.invoke(args=['reconcile-uploads', '--apply'])
    assert result.exit_code == 0, result.output
    assert path.exists() is committed
    assert record['state'] == ('committed' if committed else 'cancelled')
    assert bool(world.videos.videos) is committed
    assert runner.invoke(args=['reconcile-uploads', '--apply']).exit_code == 0


def test_reservation_unknown_result_is_recoverable_without_having_read_a_file(world):
    world.uploads.reserve_error_after = True
    assert world.user().upload().status_code == 503
    assert not world.probe_calls
    assert next(iter(world.uploads.reservations.values()))['state'] == 'reserved'
    result = world.app.test_cli_runner().invoke(args=['reconcile-uploads', '--apply'])
    assert result.exit_code == 0
    assert next(iter(world.uploads.reservations.values()))['state'] == 'cancelled'


def test_active_upload_is_never_released_by_reconciliation_and_late_completion_is_fenced(world):
    actor = world.user()
    manager = configure(world, MAX_PENDING_VIDEOS=1)
    with world.app.app_context():
        attempt = manager.begin(actor.id)
        stream = attempt.lease.spool()
        stream.write(b'partial')
        stream.flush()
        before = deepcopy(world.uploads.reservations)
        rows, _ = manager.reconcile(apply=True)
        assert rows == [(attempt.metadata['_id'], 'active')]
        assert world.uploads.reservations == before
        assert list(attempt.lease.directory.glob('*.part'))
        attempt.close()  # process exit releases its descriptor; reservation remains
        rows, _ = manager.reconcile(apply=True)
        assert rows == [(attempt.metadata['_id'], 'cancelled')]
        video = new_video(actor.id, {'caption': 'late', 'category': 'louvor'}, {
            'storage_key': attempt.key, 'bytes': 1, **PROBE_RESULT}, world.time[0])
        with pytest.raises(StorageUnavailable):
            world.uploads.complete(attempt.metadata, video)
    assert not world.videos.videos
    assert actor.upload().status_code == 201


def test_file_quota_release_is_conservative_when_database_fails_after_unlink(world):
    actor = world.user()
    manager = configure(world, MAX_USER_STORAGE_BYTES=len(MP4_BYTES), MAX_VIDEO_BYTES=len(MP4_BYTES))
    video = world.video(actor)
    world.uploads.down = True
    assert actor.delete(f'/api/videos/{video["_id"]}').status_code == 204
    assert not world.storage.path(video['file']['storage_key']).exists()
    assert world.videos.videos[video['_id']].get('file_removed_at') is None
    world.uploads.down = False
    assert actor.upload().status_code == 409
    with world.app.app_context():
        assert purge_pending() == 1
        assert purge_pending() == 0
    assert world.videos.videos[video['_id']]['file_removed_at'] is not None
    assert actor.upload().status_code == 201


def test_terminal_cleanup_retires_manifest_before_unlock_and_concurrent_reconcile(world, monkeypatch):
    actor = world.user()
    manager = configure(world, MAX_PENDING_VIDEOS=1)
    with world.app.app_context():
        attempt = manager.begin(actor.id)
        folder = attempt.lease.directory
        original_close, original_unlink = attempt.lease.close, Path.unlink
        events, recovered = [], []

        def check_unlink(path, *args, **kwargs):
            if path.parent == folder and path.name == 'manifest.json':
                assert attempt.lease.lock is not None
                with pytest.raises(LeaseBusy):
                    UploadLease(world.storage.root, attempt.metadata['_id'])
                events.append('manifest-locked')
            if path.parent == folder and path.name == 'lease.lock':
                assert attempt.lease.lock is None  # Windows cannot unlink an open lock file.
                events.append('lock-closed')
            return original_unlink(path, *args, **kwargs)

        def interleave_close():
            original_close()
            if recovered:
                return
            assert not (folder / 'manifest.json').exists()
            recovered.extend(manager.reconcile(apply=True)[0])

        monkeypatch.setattr(Path, 'unlink', check_unlink)
        monkeypatch.setattr(attempt.lease, 'close', interleave_close)
        attempt.abort()
        assert recovered == [(attempt.metadata['_id'], 'unavailable')]
        assert events == ['manifest-locked', 'lock-closed']
        assert world.uploads.reservations[attempt.metadata['_id']]['state'] == 'cancelled'
        assert not folder.exists()
    assert actor.upload().status_code == 201


def test_cancelled_recovery_with_partial_cleanup_never_reacquires_quota(world, monkeypatch):
    actor = world.user()
    manager = configure(world, MAX_PENDING_VIDEOS=1)
    with world.app.app_context():
        attempt = manager.begin(actor.id)
        (attempt.lease.directory / 'leftover.part').write_bytes(b'partial')
        world.uploads.cancel(attempt.metadata)
        attempt.close()
        original_discard = UploadLease.discard_payload

        def fail_old_workspace(lease):
            if lease.id == attempt.metadata['_id']:
                raise StorageUnavailable()
            return original_discard(lease)

        monkeypatch.setattr(UploadLease, 'discard_payload', fail_old_workspace)
        before = deepcopy(world.uploads.reservations[attempt.metadata['_id']])
        rows, _ = manager.reconcile(apply=True)
        assert rows == [(attempt.metadata['_id'], 'unavailable')]
        assert world.uploads.reservations[attempt.metadata['_id']] == before
        assert before['state'] == 'cancelled' and before['state'] not in ACTIVE
        admitted = manager.begin(actor.id)
        admitted.abort()
        monkeypatch.setattr(UploadLease, 'discard_payload', original_discard)
        assert manager.reconcile(apply=True)[0] == [(attempt.metadata['_id'], 'cancelled')]
        assert not attempt.lease.directory.exists()


def test_cancelled_leftover_cannot_delete_confirmed_media(world):
    world.uploads.complete_error_after = True
    assert world.user().upload().status_code == 503
    record = next(iter(world.uploads.reservations.values()))
    record['state'] = 'cancelled'
    path = world.storage.path(record['storage_key'])
    with world.app.app_context():
        assert world.app.extensions['upload_manager'].reconcile(apply=True)[0] == [(record['_id'], 'kept')]
    assert record['state'] == 'cancelled'
    assert path.exists() and len(world.videos.videos) == 1


@pytest.mark.parametrize('key,value', [
    ('MAX_USER_STORAGE_BYTES', 0), ('MAX_USER_STORAGE_BYTES', True),
    ('MAX_PENDING_VIDEOS', -1), ('MAX_PENDING_VIDEOS', 1.5),
    ('MAX_STORAGE_USED_PERCENT', 101), ('MAX_STORAGE_USED_PERCENT', 0),
    ('MIN_STORAGE_FREE_BYTES', -1), ('MIN_STORAGE_FREE_BYTES', True),
    ('UPLOAD_VOLUME_ID', '../other'), ('UPLOAD_VOLUME_ID', ''),
])
def test_invalid_upload_policy_fails_at_startup(tmp_path, key, value):
    with pytest.raises(ValueError):
        create_app({'APP_ENV': 'testing', 'TESTING': True, 'MONGODB_URI': '',
                    'UPLOAD_DIR': tmp_path / 'uploads', 'CACHE_SQLITE_PATH': tmp_path / 'cache', key: value})
