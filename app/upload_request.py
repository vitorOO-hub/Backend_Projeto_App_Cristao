"""Multipart files use the reserved media volume, including partial-parser cleanup."""
from flask import Request


class UploadRequest(Request):
    upload_attempt = None

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.upload_attempt is not None:
            return self.upload_attempt.lease.spool()
        return super()._get_file_stream(total_content_length, content_type, filename, content_length)

    def close(self):
        try:
            super().close()
        finally:
            if self.upload_attempt is not None:
                self.upload_attempt.lease.close_spools()
