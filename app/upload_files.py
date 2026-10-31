"""Private upload workspaces with process-owned, cross-worker recovery locks."""
import errno
import heapq
import json
import os
import re
import tempfile
from pathlib import Path

from bson import ObjectId

from app.errors import StorageUnavailable

LEASE_ID = re.compile(r'[0-9a-f]{32}')


class LeaseBusy(Exception):
    pass


def lock_file(stream):
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            raise LeaseBusy() from None
        raise StorageUnavailable() from None
    except ImportError:
        raise StorageUnavailable() from None


def unlock_file(stream):
    if os.name == 'nt':
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class UploadLease:
    def __init__(self, root, lease_id, *, create=False):
        if not isinstance(lease_id, str) or not LEASE_ID.fullmatch(lease_id):
            raise ValueError('Identificador de reserva inválido.')
        self.id = lease_id
        self.directory = Path(root) / '.upload-leases' / lease_id
        self.streams, self.lock = [], None
        try:
            if create:
                self.directory.mkdir(parents=True, exist_ok=False)
                with open(self.directory / 'lease.lock', 'xb') as stream:
                    stream.write(b'0')
            if self.directory.is_symlink():
                raise StorageUnavailable()
            self.lock = open(self.directory / 'lease.lock', 'r+b')
            lock_file(self.lock)
        except (LeaseBusy, StorageUnavailable):
            if self.lock:
                self.lock.close()
            self.lock = None
            raise
        except OSError:
            if self.lock:
                self.lock.close()
            self.lock = None
            raise StorageUnavailable() from None

    def write_manifest(self, metadata):
        try:
            with open(self.directory / 'manifest.json', 'x', encoding='utf-8') as stream:
                json.dump({**metadata, 'author_id': str(metadata['author_id'])}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise StorageUnavailable() from None

    def read_manifest(self):
        try:
            with open(self.directory / 'manifest.json', encoding='utf-8') as stream:
                metadata = json.load(stream)
            if (metadata['_id'] != self.id or metadata['storage_key'] != self.id + '.mp4'
                    or not isinstance(metadata['volume_id'], str)
                    or not isinstance(metadata['author_id'], str)
                    or not re.fullmatch(r'[0-9a-f]{24}', metadata['author_id'])):
                raise ValueError
            metadata['author_id'] = ObjectId(metadata['author_id'])
            return metadata
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageUnavailable() from None

    def spool(self):
        try:
            stream = tempfile.NamedTemporaryFile(prefix='spool-', suffix='.part', dir=self.directory)
            self.streams.append(stream)
            return stream
        except OSError:
            raise StorageUnavailable() from None

    def close_spools(self):
        for stream in self.streams:
            stream.close()
        self.streams.clear()

    def discard_payload(self):
        try:
            self.close_spools()
            for path in self.directory.iterdir():
                if path.name not in ('lease.lock', 'manifest.json'):
                    path.unlink(missing_ok=True)
        except OSError:
            raise StorageUnavailable() from None

    def close(self):
        try:
            self.close_spools()
        finally:
            if self.lock is not None:
                try:
                    unlock_file(self.lock)
                finally:
                    self.lock.close()
                    self.lock = None

    def cleanup_terminal(self):
        """Only after a terminal database decision; UUID directories are never reused."""
        self.discard_payload()
        self.close()
        try:
            (self.directory / 'lease.lock').unlink(missing_ok=True)
            (self.directory / 'manifest.json').unlink(missing_ok=True)
            self.directory.rmdir()
        except OSError:
            # Metadata-only leftovers do not reserve quota and can be diagnosed later.
            pass


def lease_page(root, *, limit=50, after=None):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('Limite de reconciliação inválido.')
    if after is not None and not LEASE_ID.fullmatch(after):
        raise ValueError('Cursor de reconciliação inválido.')
    directory = Path(root) / '.upload-leases'
    try:
        if not directory.exists():
            return [], False
        names = heapq.nsmallest(limit + 1, (path.name for path in directory.iterdir()
                              if LEASE_ID.fullmatch(path.name) and (after is None or path.name > after)))
        return names[:limit], len(names) > limit
    except OSError:
        raise StorageUnavailable() from None
