"""Private on-disk storage for MP4 files; names are generated here, never taken from a client."""
import os
import uuid
from pathlib import Path

from app.videos import STORAGE_KEY

CHUNK_BYTES = 1024 * 1024


class FileTooLarge(Exception):
    pass


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.temp = self.root / 'tmp'
        self.temp.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_key():
        return uuid.uuid4().hex + '.mp4'

    def path(self, key):
        if not isinstance(key, str) or not STORAGE_KEY.fullmatch(key):
            raise ValueError('Chave de armazenamento inválida.')
        return self.root / key

    def receive(self, stream, max_bytes, *, temp_dir=None):
        """Copy an upload to a temporary file, stopping as soon as it exceeds `max_bytes`."""
        path = Path(temp_dir or self.temp) / (uuid.uuid4().hex + '.part')
        size = 0
        try:
            with open(path, 'xb') as output:
                while chunk := stream.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge()
                    output.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, size

    def commit(self, temp, key):
        os.replace(temp, self.path(key))

    def discard(self, temp):
        Path(temp).unlink(missing_ok=True)

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)
