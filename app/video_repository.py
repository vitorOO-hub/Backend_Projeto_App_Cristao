"""Mongo persistence for video metadata; the MP4 files live in storage, never in MongoDB."""
from datetime import datetime
from typing import Protocol

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.constants import CATEGORIES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, VIDEO_STATUSES
from app.errors import DataUnavailable
from app.videos import EMPTY_REVIEW, METADATA_FIELDS, to_millis

NEWEST_FIRST = [('created_at', -1), ('_id', -1)]
OLDEST_FIRST = [('created_at', 1), ('_id', 1)]
# Decisions are administrative actions: approve pending/rejected, reject pending/approved.
DECISION_FROM = {'approved': ('pending', 'rejected'), 'rejected': ('pending', 'approved')}


class VideoRepository(Protocol):
    def insert(self, video): ...
    def find_by_id(self, video_id): ...
    def list_approved(self, *, category=None, author_id=None, exclude_authors=(),
                      limit=DEFAULT_PAGE_SIZE, after=None): ...
    def list_by_author(self, author_id, *, status=None, limit=DEFAULT_PAGE_SIZE, after=None): ...
    def list_for_review(self, *, status='pending', limit=DEFAULT_PAGE_SIZE, after=None): ...
    def update_metadata(self, video_id, author_id, fields, at): ...
    def decide(self, video_id, *, expected_updated_at, status, reason, admin_id, at): ...
    def soft_delete(self, video_id, author_id, at): ...
    def list_pending_purge(self, limit=50): ...
    def mark_purged(self, video_id, at): ...


class UnavailableVideoRepository:
    def _unavailable(self, *args, **kwargs):
        raise DataUnavailable()

    insert = find_by_id = list_approved = list_by_author = list_for_review = _unavailable
    update_metadata = decide = soft_delete = list_pending_purge = mark_purged = _unavailable


def object_id(value):
    """Identifiers reach the repository already parsed; a string or operator is a caller bug."""
    if not isinstance(value, ObjectId):
        raise ValueError('Identificador deve ser ObjectId, sem operadores fornecidos pelo cliente.')
    return value


def page_limit(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError('Limite de página inválido.')
    return limit


def purge_limit(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('Limite de limpeza inválido.')
    return limit


def cursor_clause(after, descending):
    """Keyset position `(created_at, _id)` of the last item already delivered."""
    if (not isinstance(after, tuple) or len(after) != 2 or not isinstance(after[0], datetime)
            or not isinstance(after[1], ObjectId)):
        raise ValueError('Cursor deve ser (created_at, _id).')
    op = '$lt' if descending else '$gt'
    return {'$or': [{'created_at': {op: after[0]}},
                    {'created_at': after[0], '_id': {op: after[1]}}]}


def fetch_page(collection, query, order, limit, after):
    """Keyset page of `limit` items plus a has_more flag (one extra item is fetched)."""
    limit = page_limit(limit)
    if after is not None:
        query = {'$and': [query, cursor_clause(after, order == NEWEST_FIRST)]}
    try:
        items = list(collection.find(query).sort(order).limit(limit + 1))
    except PyMongoError:
        raise DataUnavailable() from None
    return items[:limit], len(items) > limit


def approved_query(category=None, author_id=None, exclude_authors=()):
    """The one definition of what the public feed may show."""
    query = {'status': 'approved', 'deleted_at': None}
    if category is not None:
        if category not in CATEGORIES:
            raise ValueError('Categoria inválida.')
        query['category'] = category
    authors = {}
    if author_id is not None:
        authors['$eq'] = object_id(author_id)
    excluded = [object_id(value) for value in exclude_authors]
    if excluded:
        authors['$nin'] = excluded
    if authors:
        query['author_id'] = authors
    return query


def check_status(status):
    if status not in VIDEO_STATUSES:
        raise ValueError('Estado inválido.')
    return status


class MongoVideoRepository:
    def __init__(self, database):
        self.videos = database['videos']

    def insert(self, video):
        try:
            self.videos.insert_one(video)
        except PyMongoError:
            raise DataUnavailable() from None
        return video

    def find_by_id(self, video_id):
        """Any state except deleted; the caller decides visibility with videos.can_view."""
        object_id(video_id)
        try:
            return self.videos.find_one({'_id': video_id, 'deleted_at': None})
        except PyMongoError:
            raise DataUnavailable() from None

    def page(self, query, order, limit, after):
        return fetch_page(self.videos, query, order, limit, after)

    def list_approved(self, *, category=None, author_id=None, exclude_authors=(),
                      limit=DEFAULT_PAGE_SIZE, after=None):
        return self.page(approved_query(category, author_id, exclude_authors), NEWEST_FIRST, limit, after)

    def list_by_author(self, author_id, *, status=None, limit=DEFAULT_PAGE_SIZE, after=None):
        """The author's own videos in every state (GET /me/videos); never lists deleted ones."""
        query = {'author_id': object_id(author_id), 'deleted_at': None}
        if status is not None:
            query['status'] = check_status(status)
        return self.page(query, NEWEST_FIRST, limit, after)

    def list_for_review(self, *, status='pending', limit=DEFAULT_PAGE_SIZE, after=None):
        """Moderation queue, oldest first so waiting items are not starved."""
        query = {'status': check_status(status), 'deleted_at': None}
        return self.page(query, OLDEST_FIRST, limit, after)

    def update_metadata(self, video_id, author_id, fields, at):
        """Author edit in one atomic step; an approved video goes back to pending and loses its review."""
        object_id(video_id)
        object_id(author_id)
        if not isinstance(fields, dict) or not fields or not set(fields) <= METADATA_FIELDS:
            raise ValueError('Edição aceita somente legenda, categoria e referência bíblica.')
        # $literal: client text beginning with "$" must never be evaluated as an expression.
        stage = {key: {'$literal': value} for key, value in fields.items()}
        approved = {'$eq': ['$status', 'approved']}
        stage['updated_at'] = {'$literal': to_millis(at)}
        stage['status'] = {'$cond': [approved, 'pending', '$status']}
        stage['review'] = {'$cond': [approved, {'$literal': EMPTY_REVIEW}, '$review']}
        try:
            return self.videos.find_one_and_update(
                {'_id': video_id, 'author_id': author_id, 'deleted_at': None},
                [{'$set': stage}], return_document=ReturnDocument.AFTER)
        except PyMongoError:
            raise DataUnavailable() from None

    def decide(self, video_id, *, expected_updated_at, status, reason, admin_id, at):
        """Conditional on `updated_at`: returns None if the video changed, vanished or cannot make the transition."""
        object_id(video_id)
        object_id(admin_id)
        if status not in DECISION_FROM or not isinstance(reason, str) or not isinstance(expected_updated_at, datetime):
            raise ValueError('Decisão inválida.')
        at = to_millis(at)
        try:
            return self.videos.find_one_and_update(
                {'_id': video_id, 'deleted_at': None, 'updated_at': expected_updated_at,
                 'status': {'$in': list(DECISION_FROM[status])}},
                {'$set': {'status': status, 'updated_at': at,
                          'review': {'reason': reason, 'decided_by': admin_id, 'decided_at': at}}},
                return_document=ReturnDocument.AFTER)
        except PyMongoError:
            raise DataUnavailable() from None

    def soft_delete(self, video_id, author_id, at):
        """Immediate invisibility; the file and relations are removed afterwards by the purge."""
        object_id(video_id)
        object_id(author_id)
        at = to_millis(at)
        try:
            result = self.videos.update_one(
                {'_id': video_id, 'author_id': author_id, 'deleted_at': None},
                {'$set': {'deleted_at': at, 'updated_at': at}})
        except PyMongoError:
            raise DataUnavailable() from None
        return result.matched_count == 1

    def list_pending_purge(self, limit=50):
        try:
            return list(self.videos.find({'deleted_at': {'$type': 'date'}, 'purged_at': None})
                        .sort('deleted_at', 1).limit(purge_limit(limit)))
        except PyMongoError:
            raise DataUnavailable() from None

    def mark_purged(self, video_id, at):
        """Idempotent: a second call finds nothing to change."""
        object_id(video_id)
        try:
            result = self.videos.update_one(
                {'_id': video_id, 'deleted_at': {'$type': 'date'}, 'purged_at': None},
                {'$set': {'purged_at': to_millis(at)}})
        except PyMongoError:
            raise DataUnavailable() from None
        return result.matched_count == 1
