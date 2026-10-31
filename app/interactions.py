"""Interaction writes serialized with the video's moderation, edit and deletion writes."""
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.errors import ApiError, DataUnavailable, ReportConflict
from app.transactions import MongoTransactions
from app.video_repository import object_id
from app.videos import to_millis


def unavailable_video():
    return ApiError(404, 'not_found', 'Recurso não encontrado.')


class UnavailableInteractions:
    def _unavailable(self, *args, **kwargs):
        raise DataUnavailable()

    react = unreact = comment = report = _unavailable


class MongoInteractions:
    def __init__(self, database, transactions=None):
        self.database = database
        self.transactions = transactions or MongoTransactions(database)

    def _write(self, video_id, operation, *, report=False, retry_duplicate=False):
        object_id(video_id)

        def write(session):
            # A read-only snapshot would permit a stale approved video. This write
            # conflicts with every edit, moderation decision and soft deletion.
            guard = self.database['videos'].update_one(
                {'_id': video_id, 'status': 'approved', 'deleted_at': None},
                {'$inc': {'interaction_version': 1}}, session=session)
            if guard.matched_count != 1:
                raise unavailable_video()
            return operation(session)

        for attempt in range(2 if retry_duplicate else 1):
            try:
                return self.transactions.run(write)
            except DuplicateKeyError:
                if report:
                    raise ReportConflict() from None
                if not retry_duplicate or attempt:
                    raise DataUnavailable() from None
                # An idempotent reaction racing with an existing pair retries the
                # complete transaction, including the active-video guard.
            except PyMongoError:
                raise DataUnavailable() from None

    def react(self, user_id, video_id, at):
        object_id(user_id)
        return self._write(video_id, lambda session: self.database['reactions'].update_one(
            {'user_id': user_id, 'video_id': video_id},
            {'$setOnInsert': {'created_at': to_millis(at)}}, upsert=True, session=session),
            retry_duplicate=True)

    def unreact(self, user_id, video_id):
        object_id(user_id)
        return self._write(video_id, lambda session: self.database['reactions'].delete_one(
            {'user_id': user_id, 'video_id': video_id}, session=session))

    def comment(self, document):
        object_id(document['author_id'])
        return self._write(document['video_id'],
                           lambda session: self.database['comments'].insert_one(document, session=session))

    def report(self, document):
        object_id(document['reporter_id'])
        return self._write(document['video_id'],
                           lambda session: self.database['reports'].insert_one(document, session=session),
                           report=True)
