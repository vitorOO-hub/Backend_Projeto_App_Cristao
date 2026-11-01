"""Read-only, bounded diagnosis of interactions without an active parent video."""
from pymongo.errors import PyMongoError

from app.core.errors import DataUnavailable
from app.repositories.videos import object_id, purge_limit


class UnavailableOrphanRepository:
    def list_for(self, *args, **kwargs):
        raise DataUnavailable()


class MongoOrphanRepository:
    def __init__(self, database):
        self.database = database

    def list_for(self, collection, *, limit=50, after=None):
        if collection not in ('comments', 'reactions'):
            raise ValueError('Somente comentários e reações fazem parte do diagnóstico.')
        purge_limit(limit)
        query = {'_id': {'$gt': object_id(after)}} if after is not None else {}
        pipeline = [
            {'$match': query}, {'$sort': {'_id': 1}},
            {'$lookup': {'from': 'videos', 'localField': 'video_id', 'foreignField': '_id',
                         'pipeline': [{'$match': {'deleted_at': None}}, {'$project': {'_id': 1}}],
                         'as': '_active_video'}},
            {'$match': {'_active_video': {'$size': 0}}},
            {'$limit': limit + 1}, {'$project': {'_id': 1, 'video_id': 1}},
        ]
        try:
            rows = list(self.database[collection].aggregate(pipeline, maxTimeMS=5000))
        except PyMongoError:
            raise DataUnavailable() from None
        return rows[:limit], len(rows) > limit
