"""Conservative upload admission and recovery for one host and one private media volume."""
import re
import shutil
from dataclasses import dataclass
from uuid import uuid4

from pymongo.errors import DuplicateKeyError, PyMongoError

from app.auth import now
from app.errors import ApiError, DataUnavailable, StorageUnavailable
from app.transactions import MongoTransactions
from app.upload_files import LeaseBusy, UploadLease, lease_page
from app.video_repository import object_id
from app.videos import to_millis

ACTIVE = ('reserved', 'recovering')


@dataclass(frozen=True)
class UploadPolicy:
    user_bytes: int
    pending: int
    used_percent: int
    free_bytes: int
    volume: str

    @classmethod
    def from_config(cls, config):
        for name in ('MAX_USER_STORAGE_BYTES', 'MAX_PENDING_VIDEOS', 'MAX_STORAGE_USED_PERCENT'):
            if type(config[name]) is not int or config[name] <= 0:
                raise ValueError(f'Configuração inválida: {name}.')
        if (config['MAX_STORAGE_USED_PERCENT'] > 100 or type(config['MIN_STORAGE_FREE_BYTES']) is not int
                or config['MIN_STORAGE_FREE_BYTES'] < 0):
            raise ValueError('Limites de capacidade inválidos.')
        volume = config['UPLOAD_VOLUME_ID']
        if not isinstance(volume, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', volume):
            raise ValueError('UPLOAD_VOLUME_ID inválido.')
        return cls(config['MAX_USER_STORAGE_BYTES'], config['MAX_PENDING_VIDEOS'],
                   config['MAX_STORAGE_USED_PERCENT'], config['MIN_STORAGE_FREE_BYTES'], volume)


def check_admission(policy, used, pending, user_reserved, slots_reserved, volume_reserved,
                    user_bytes, volume_bytes, disk):
    if used + user_reserved + user_bytes > policy.user_bytes:
        raise ApiError(409, 'storage_quota_exceeded',
                       'Você atingiu a cota de armazenamento da conta. Exclua vídeos e aguarde a limpeza dos arquivos para enviar novos.')
    if pending + slots_reserved >= policy.pending:
        raise ApiError(409, 'pending_video_limit',
                       f'Você já tem {policy.pending} vídeos em análise ou envio. Aguarde a revisão antes de enviar outro.')
    projected_free = disk.free - volume_reserved - volume_bytes
    projected_used = disk.used + volume_reserved + volume_bytes
    if (disk.total <= 0 or projected_free < policy.free_bytes
            or projected_used * 100 > disk.total * policy.used_percent):
        raise StorageUnavailable()


class UnavailableUploadRepository:
    def _unavailable(self, *args, **kwargs):
        raise DataUnavailable()

    reserve = complete = cancel = inspect = prepare_recovery = mark_file_removed = _unavailable


class MongoUploadRepository:
    def __init__(self, database, transactions=None):
        self.database = database
        self.transactions = transactions or MongoTransactions(database)

    def _run(self, metadata, operation):
        keys = ('volume:' + metadata['volume_id'], 'account:' + str(object_id(metadata['author_id'])))
        try:
            for key in keys:
                try:
                    self.database['upload_guards'].update_one(
                        {'_id': key}, {'$setOnInsert': {'revision': 0}}, upsert=True)
                except DuplicateKeyError:
                    pass

            def guarded(session):
                for key in keys:
                    self.database['upload_guards'].update_one(
                        {'_id': key}, {'$inc': {'revision': 1}}, session=session)
                return operation(session)

            return self.transactions.run(guarded)
        except PyMongoError:
            raise DataUnavailable() from None

    @staticmethod
    def _sum(collection, match, fields, session):
        rows = list(collection.aggregate([
            {'$match': match}, {'$group': {'_id': None, **fields}}], session=session))
        return rows[0] if rows else {}

    def reserve(self, metadata, policy, user_bytes, volume_bytes, capacity):
        def reserve(session):
            videos = self._sum(self.database['videos'], {
                'author_id': metadata['author_id'], 'purged_at': None, 'file_removed_at': None},
                {'bytes': {'$sum': '$file.bytes'}}, session)
            pending = self.database['videos'].count_documents({
                'author_id': metadata['author_id'], 'status': 'pending', 'deleted_at': None}, session=session)
            own = self._sum(self.database['upload_reservations'], {
                'author_id': metadata['author_id'], 'state': {'$in': list(ACTIVE)}},
                {'bytes': {'$sum': '$user_bytes'}, 'slots': {'$sum': '$pending_slots'}}, session)
            volume = self._sum(self.database['upload_reservations'], {
                'volume_id': metadata['volume_id'], 'state': {'$in': list(ACTIVE)}},
                {'bytes': {'$sum': '$volume_bytes'}}, session)
            # Sample after acquiring the shared volume guard, so a completed upload
            # cannot release its reservation between an old sample and this decision.
            disk = capacity()
            check_admission(policy, videos.get('bytes', 0), pending, own.get('bytes', 0),
                            own.get('slots', 0), volume.get('bytes', 0), user_bytes, volume_bytes, disk)
            self.database['upload_reservations'].insert_one({
                **metadata, 'state': 'reserved', 'user_bytes': user_bytes, 'volume_bytes': volume_bytes,
                'pending_slots': 1, 'created_at': to_millis(now())}, session=session)
        return self._run(metadata, reserve)

    @staticmethod
    def _matches(record, metadata):
        return record is None or all(record.get(key) == metadata[key]
                                     for key in ('author_id', 'volume_id', 'storage_key'))

    def complete(self, metadata, video):
        def complete(session):
            reservations = self.database['upload_reservations']
            record = reservations.find_one({'_id': metadata['_id']}, session=session)
            if (record is None or not self._matches(record, metadata) or record['state'] != 'reserved'
                    or video['author_id'] != metadata['author_id']
                    or video['file']['storage_key'] != metadata['storage_key']
                    or video['file']['bytes'] > record['user_bytes']):
                raise StorageUnavailable()
            self.database['videos'].insert_one(video, session=session)
            reservations.update_one({'_id': metadata['_id'], 'state': 'reserved'},
                                    {'$set': {'state': 'committed', 'video_id': video['_id'],
                                              'completed_at': to_millis(now())}}, session=session)
        return self._run(metadata, complete)

    def cancel(self, metadata):
        def cancel(session):
            reservations = self.database['upload_reservations']
            record = reservations.find_one({'_id': metadata['_id']}, session=session)
            if not self._matches(record, metadata) or record is not None and record['state'] == 'committed':
                raise StorageUnavailable()
            if record is None:
                # A tombstone fences a delayed reservation transaction after an
                # ambiguous result; this UUID must never be admitted again.
                reservations.insert_one({**metadata, 'state': 'cancelled', 'user_bytes': 0,
                                         'volume_bytes': 0, 'pending_slots': 0,
                                         'completed_at': to_millis(now())}, session=session)
            else:
                reservations.update_one({'_id': metadata['_id']},
                                        {'$set': {'state': 'cancelled', 'completed_at': to_millis(now())}},
                                        session=session)
        return self._run(metadata, cancel)

    def inspect(self, metadata):
        try:
            record = self.database['upload_reservations'].find_one({'_id': metadata['_id']})
            if not self._matches(record, metadata):
                raise StorageUnavailable()
            video = self.database['videos'].find_one({'file.storage_key': metadata['storage_key']})
            return 'committed' if video is not None else (record['state'] if record else 'unreserved')
        except PyMongoError:
            raise DataUnavailable() from None

    def prepare_recovery(self, metadata):
        def prepare(session):
            reservations = self.database['upload_reservations']
            record = reservations.find_one({'_id': metadata['_id']}, session=session)
            if not self._matches(record, metadata):
                raise StorageUnavailable()
            video = self.database['videos'].find_one({'file.storage_key': metadata['storage_key']}, session=session)
            if video is not None:
                if record is not None and record['state'] != 'cancelled':
                    reservations.update_one({'_id': metadata['_id']},
                                            {'$set': {'state': 'committed', 'video_id': video['_id'],
                                                      'completed_at': to_millis(now())}}, session=session)
                return 'keep'
            if record is not None and record['state'] == 'committed':
                return 'manual'
            if record is not None and record['state'] == 'cancelled':
                # Cleanup of terminal leftovers must never reacquire quota. The
                # workspace may disappear immediately after its prior owner
                # releases the file lock.
                return 'discard'
            if record is None:
                reservations.insert_one({**metadata, 'state': 'recovering', 'user_bytes': 0,
                                         'volume_bytes': 0, 'pending_slots': 0,
                                         'created_at': to_millis(now())}, session=session)
            else:
                reservations.update_one({'_id': metadata['_id']}, {'$set': {'state': 'recovering'}}, session=session)
            return 'discard'
        return self._run(metadata, prepare)

    def mark_file_removed(self, video, volume_id):
        metadata = {'author_id': video['author_id'], 'volume_id': volume_id}

        def mark(session):
            self.database['videos'].update_one(
                {'_id': video['_id'], 'deleted_at': {'$type': 'date'}, 'file_removed_at': None},
                {'$set': {'file_removed_at': to_millis(now())}}, session=session)
        return self._run(metadata, mark)


class UploadAttempt:
    def __init__(self, manager, metadata, lease):
        self.manager, self.metadata, self.lease = manager, metadata, lease
        self.moved = self.committed = False

    @property
    def key(self):
        return self.metadata['storage_key']

    def publish(self, temp, video):
        self.moved = True
        self.manager.storage.commit(temp, self.key)
        # Any failure from here is ambiguous. Keep the file and reservation for
        # fenced reconciliation rather than deleting a possibly committed video.
        self.manager.repository.complete(self.metadata, video)
        self.committed = True
        self.lease.cleanup_terminal()

    def abort(self):
        if not self.moved:
            self.lease.discard_payload()
            self.manager.repository.cancel(self.metadata)
            self.lease.cleanup_terminal()

    def close(self):
        self.lease.close()


class UploadManager:
    def __init__(self, storage, repository, config, capacity_probe=None):
        self.storage, self.repository, self.config = storage, repository, config
        self.policy = UploadPolicy.from_config(config)
        self.capacity_probe = capacity_probe or (lambda: shutil.disk_usage(self.storage.root))

    def begin(self, author_id):
        object_id(author_id)
        identity = uuid4().hex
        metadata = {'_id': identity, 'author_id': author_id, 'storage_key': identity + '.mp4',
                    'volume_id': self.policy.volume}
        lease = UploadLease(self.storage.root, identity, create=True)
        try:
            lease.write_manifest(metadata)

            def capacity():
                try:
                    return self.capacity_probe()
                except OSError:
                    raise StorageUnavailable() from None

            self.repository.reserve(metadata, self.policy, self.config['MAX_VIDEO_BYTES'],
                                    2 * self.config['MAX_CONTENT_LENGTH'], capacity)
            return UploadAttempt(self, metadata, lease)
        except (ApiError, StorageUnavailable):
            # These failures abort admission before it can commit a reservation.
            lease.cleanup_terminal()
            raise
        except BaseException:
            # A failed database acknowledgement may already have reserved resources.
            # Keep its locked workspace identifiable until explicit reconciliation.
            lease.close()
            raise

    def file_removed(self, video):
        self.repository.mark_file_removed(video, self.policy.volume)

    def reconcile(self, *, apply=False, limit=50, after=None):
        identities, more = lease_page(self.storage.root, limit=limit, after=after)
        rows = []
        for identity in identities:
            lease = None
            try:
                lease = UploadLease(self.storage.root, identity)
                metadata = lease.read_manifest()
                if metadata['volume_id'] != self.policy.volume:
                    rows.append((identity, 'other-volume'))
                    continue
                if not apply:
                    rows.append((identity, self.repository.inspect(metadata)))
                    continue
                action = self.repository.prepare_recovery(metadata)
                if action == 'discard':
                    self.storage.delete(metadata['storage_key'])
                    lease.discard_payload()
                    self.repository.cancel(metadata)
                    lease.cleanup_terminal()
                    rows.append((identity, 'cancelled'))
                elif action == 'keep':
                    lease.cleanup_terminal()
                    rows.append((identity, 'kept'))
                else:
                    rows.append((identity, 'manual'))
            except LeaseBusy:
                rows.append((identity, 'active'))
            except StorageUnavailable:
                rows.append((identity, 'unavailable'))
            finally:
                if lease is not None:
                    lease.close()
        return rows, identities[-1] if more and identities else None
