"""Mongo persistence for reactions, comments, reports and blocks; no local fallback."""
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.constants import DEFAULT_PAGE_SIZE, REPORT_STATUSES
from app.errors import DataUnavailable, ReportConflict
from app.video_repository import NEWEST_FIRST, OLDEST_FIRST, approved_query, fetch_page, object_id
from app.videos import to_millis

MAX_BATCH = 100
MAX_EXCLUDED = 1000


def object_ids(values, limit=MAX_BATCH):
    values = list(values)
    if len(values) > limit:
        raise ValueError('Lote de identificadores grande demais.')
    return [object_id(value) for value in values]


def counts(collection, query, ids):
    """Per-video totals for one page of videos in a single aggregation."""
    if not ids:
        return {}
    pipeline = [{'$match': {**query, 'video_id': {'$in': ids}}},
                {'$group': {'_id': '$video_id', 'total': {'$sum': 1}}}]
    try:
        return {row['_id']: row['total'] for row in collection.aggregate(pipeline)}
    except PyMongoError:
        raise DataUnavailable() from None


def excluding(authors):
    authors = object_ids(authors, MAX_EXCLUDED)
    return {'author_id': {'$nin': authors}} if authors else {}


class UnavailableRepository:
    """Base for the offline stand-ins: every operation fails closed with 503."""

    def _unavailable(self, *args, **kwargs):
        raise DataUnavailable()


class UnavailableReactionRepository(UnavailableRepository):
    add = remove = count_for = reacted_by = delete_for_video = UnavailableRepository._unavailable
    list_by_user = count_by_user = count_received = UnavailableRepository._unavailable


class UnavailableCommentRepository(UnavailableRepository):
    insert = find_by_id = list_for_video = count_for = UnavailableRepository._unavailable
    delete = delete_for_video = UnavailableRepository._unavailable


class UnavailableReportRepository(UnavailableRepository):
    insert = find_by_id = list_by_status = resolve = UnavailableRepository._unavailable


class UnavailableFollowRepository(UnavailableRepository):
    add = remove = list_followers = list_following = UnavailableRepository._unavailable
    count_followers = count_following = UnavailableRepository._unavailable


class UnavailableBlockRepository(UnavailableRepository):
    add = remove = list_for = blocked_ids = exists = UnavailableRepository._unavailable


class MongoReactionRepository:
    def __init__(self, database):
        self.reactions = database['reactions']
        self.videos = database['videos']

    def add(self, user_id, video_id, at):
        """Idempotent: repeating the reaction (or racing with itself) never duplicates it."""
        object_id(user_id), object_id(video_id)
        try:
            self.reactions.update_one({'user_id': user_id, 'video_id': video_id},
                                      {'$setOnInsert': {'created_at': to_millis(at)}}, upsert=True)
        except DuplicateKeyError:
            pass  # a concurrent request inserted the same pair first: already reacted
        except PyMongoError:
            raise DataUnavailable() from None

    def remove(self, user_id, video_id):
        """Idempotent: removing a reaction that does not exist is not an error."""
        object_id(user_id), object_id(video_id)
        try:
            self.reactions.delete_one({'user_id': user_id, 'video_id': video_id})
        except PyMongoError:
            raise DataUnavailable() from None

    def count_for(self, video_ids):
        return counts(self.reactions, {}, object_ids(video_ids))

    def reacted_by(self, user_id, video_ids):
        object_id(user_id)
        ids = object_ids(video_ids)
        if not ids:
            return set()
        try:
            rows = self.reactions.find({'user_id': user_id, 'video_id': {'$in': ids}}, {'video_id': 1})
            return {row['video_id'] for row in rows}
        except PyMongoError:
            raise DataUnavailable() from None

    def list_by_user(self, user_id, *, limit=DEFAULT_PAGE_SIZE, after=None):
        """The user's reactions, newest first (profile "Curtidos"); video visibility is the caller's job."""
        return fetch_page(self.reactions, {'user_id': object_id(user_id)}, NEWEST_FIRST, limit, after)

    def count_by_user(self, user_id):
        object_id(user_id)
        try:
            return self.reactions.count_documents({'user_id': user_id})
        except PyMongoError:
            raise DataUnavailable() from None

    def count_received(self, author_id):
        """Reactions on the author's approved, undeleted videos, in one aggregation."""
        pipeline = [
            {'$match': approved_query(author_id=object_id(author_id))},
            {'$lookup': {'from': 'reactions', 'let': {'video': '$_id'}, 'as': 'received',
                         'pipeline': [{'$match': {'$expr': {'$eq': ['$video_id', '$$video']}}},
                                      {'$count': 'total'}]}},
            {'$group': {'_id': None,
                        'total': {'$sum': {'$ifNull': [{'$arrayElemAt': ['$received.total', 0]}, 0]}}}},
        ]
        try:
            rows = list(self.videos.aggregate(pipeline))
        except PyMongoError:
            raise DataUnavailable() from None
        return rows[0]['total'] if rows else 0

    def delete_for_video(self, video_id):
        """Part of the purge of a deleted video; safe to repeat."""
        object_id(video_id)
        try:
            return self.reactions.delete_many({'video_id': video_id}).deleted_count
        except PyMongoError:
            raise DataUnavailable() from None


class MongoCommentRepository:
    def __init__(self, database):
        self.comments = database['comments']

    def insert(self, comment):
        try:
            self.comments.insert_one(comment)
        except PyMongoError:
            raise DataUnavailable() from None
        return comment

    def find_by_id(self, comment_id):
        object_id(comment_id)
        try:
            return self.comments.find_one({'_id': comment_id})
        except PyMongoError:
            raise DataUnavailable() from None

    def list_for_video(self, video_id, *, exclude_authors=(), limit=DEFAULT_PAGE_SIZE, after=None):
        """Oldest first; blocked authors are removed before the limit is applied."""
        query = {'video_id': object_id(video_id), **excluding(exclude_authors)}
        return fetch_page(self.comments, query, OLDEST_FIRST, limit, after)

    def count_for(self, video_ids, exclude_authors=()):
        return counts(self.comments, excluding(exclude_authors), object_ids(video_ids))

    def delete(self, comment_id, author_id):
        """Only the author removes a comment; returns whether one was removed."""
        object_id(comment_id), object_id(author_id)
        try:
            return self.comments.delete_one({'_id': comment_id, 'author_id': author_id}).deleted_count == 1
        except PyMongoError:
            raise DataUnavailable() from None

    def delete_for_video(self, video_id):
        object_id(video_id)
        try:
            return self.comments.delete_many({'video_id': video_id}).deleted_count
        except PyMongoError:
            raise DataUnavailable() from None


class MongoReportRepository:
    def __init__(self, database):
        self.reports = database['reports']

    def insert(self, report):
        """A second open report by the same user for the same video raises ReportConflict."""
        try:
            self.reports.insert_one(report)
        except DuplicateKeyError:
            raise ReportConflict() from None
        except PyMongoError:
            raise DataUnavailable() from None
        return report

    def find_by_id(self, report_id):
        object_id(report_id)
        try:
            return self.reports.find_one({'_id': report_id})
        except PyMongoError:
            raise DataUnavailable() from None

    def list_by_status(self, *, status='open', limit=DEFAULT_PAGE_SIZE, after=None):
        """Administrative queue, oldest first."""
        if status not in REPORT_STATUSES:
            raise ValueError('Estado inválido.')
        return fetch_page(self.reports, {'status': status}, OLDEST_FIRST, limit, after)

    def resolve(self, report_id, *, note, admin_id, at):
        """Only an open report can be resolved; returns None when it is gone or already resolved."""
        object_id(report_id), object_id(admin_id)
        if not isinstance(note, str):
            raise ValueError('Nota de resolução deve ser texto.')
        try:
            return self.reports.find_one_and_update(
                {'_id': report_id, 'status': 'open'},
                {'$set': {'status': 'resolved', 'resolution_note': note, 'resolved_by': admin_id,
                          'resolved_at': to_millis(at)}},
                return_document=ReturnDocument.AFTER)
        except PyMongoError:
            raise DataUnavailable() from None


class MongoFollowRepository:
    def __init__(self, database):
        self.follows = database['follows']

    def add(self, follower_id, followee_id, at):
        """Idempotent, like reactions and blocks; the unique pair index stops a concurrent duplicate."""
        object_id(follower_id), object_id(followee_id)
        try:
            self.follows.update_one({'follower_id': follower_id, 'followee_id': followee_id},
                                    {'$setOnInsert': {'created_at': to_millis(at)}}, upsert=True)
        except DuplicateKeyError:
            pass
        except PyMongoError:
            raise DataUnavailable() from None

    def remove(self, follower_id, followee_id):
        object_id(follower_id), object_id(followee_id)
        try:
            self.follows.delete_one({'follower_id': follower_id, 'followee_id': followee_id})
        except PyMongoError:
            raise DataUnavailable() from None

    @staticmethod
    def side(user_field, user_id, other_field, exclude_users):
        query = {user_field: object_id(user_id)}
        excluded = object_ids(exclude_users, MAX_EXCLUDED)
        if excluded:
            query[other_field] = {'$nin': excluded}
        return query

    def list_followers(self, user_id, *, exclude_users=(), limit=DEFAULT_PAGE_SIZE, after=None):
        """Who follows `user_id`, newest first; `exclude_users` (e.g. blocked ones) go before the limit."""
        query = self.side('followee_id', user_id, 'follower_id', exclude_users)
        return fetch_page(self.follows, query, NEWEST_FIRST, limit, after)

    def list_following(self, user_id, *, exclude_users=(), limit=DEFAULT_PAGE_SIZE, after=None):
        query = self.side('follower_id', user_id, 'followee_id', exclude_users)
        return fetch_page(self.follows, query, NEWEST_FIRST, limit, after)

    def count(self, query):
        try:
            return self.follows.count_documents(query)
        except PyMongoError:
            raise DataUnavailable() from None

    def count_followers(self, user_id, exclude_users=()):
        return self.count(self.side('followee_id', user_id, 'follower_id', exclude_users))

    def count_following(self, user_id, exclude_users=()):
        return self.count(self.side('follower_id', user_id, 'followee_id', exclude_users))


class MongoBlockRepository:
    def __init__(self, database):
        self.blocks = database['blocks']

    def add(self, user_id, blocked_user_id, at):
        object_id(user_id), object_id(blocked_user_id)
        try:
            self.blocks.update_one({'user_id': user_id, 'blocked_user_id': blocked_user_id},
                                   {'$setOnInsert': {'created_at': to_millis(at)}}, upsert=True)
        except DuplicateKeyError:
            pass
        except PyMongoError:
            raise DataUnavailable() from None

    def remove(self, user_id, blocked_user_id):
        object_id(user_id), object_id(blocked_user_id)
        try:
            self.blocks.delete_one({'user_id': user_id, 'blocked_user_id': blocked_user_id})
        except PyMongoError:
            raise DataUnavailable() from None

    def list_for(self, user_id, *, limit=DEFAULT_PAGE_SIZE, after=None):
        return fetch_page(self.blocks, {'user_id': object_id(user_id)}, NEWEST_FIRST, limit, after)

    def blocked_ids(self, user_id, limit=1000):
        """Everyone this user blocked, to hide their content in discovery queries."""
        object_id(user_id)
        try:
            rows = self.blocks.find({'user_id': user_id}, {'blocked_user_id': 1}).limit(limit)
            return [row['blocked_user_id'] for row in rows]
        except PyMongoError:
            raise DataUnavailable() from None

    def exists(self, user_id, blocked_user_id):
        object_id(user_id), object_id(blocked_user_id)
        try:
            return self.blocks.count_documents(
                {'user_id': user_id, 'blocked_user_id': blocked_user_id}, limit=1) == 1
        except PyMongoError:
            raise DataUnavailable() from None
