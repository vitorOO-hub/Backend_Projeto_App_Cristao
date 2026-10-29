"""Declarative indexes for the product collections, created only by `flask init-db`."""
from pymongo import IndexModel
from pymongo.errors import PyMongoError

from app.errors import DataUnavailable

# users, sessions, rate_limits, bible_cache and login_attempts keep their own initialize().
INDEXES = {
    'videos': [
        # Feed; the moderation queue (oldest first) reads this index backwards.
        IndexModel([('status', 1), ('created_at', -1), ('_id', -1)], name='videos_feed'),
        IndexModel([('status', 1), ('category', 1), ('created_at', -1), ('_id', -1)],
                   name='videos_feed_category'),
        IndexModel([('author_id', 1), ('created_at', -1), ('_id', -1)], name='videos_author'),
        # Only tombstones: finds deleted videos whose file and relations are still to be purged.
        IndexModel([('deleted_at', 1)], name='videos_purge',
                   partialFilterExpression={'deleted_at': {'$type': 'date'}}),
    ],
    'reactions': [
        IndexModel([('user_id', 1), ('video_id', 1)], unique=True, name='reactions_user_video_unique'),
        IndexModel([('video_id', 1)], name='reactions_video'),
        # "Curtidos" of the profile: a user's reactions, newest first.
        IndexModel([('user_id', 1), ('created_at', -1), ('_id', -1)], name='reactions_user'),
    ],
    'comments': [
        IndexModel([('video_id', 1), ('created_at', 1), ('_id', 1)], name='comments_video'),
    ],
    'reports': [
        IndexModel([('status', 1), ('created_at', 1), ('_id', 1)], name='reports_queue'),
        # One open report per user and video.
        IndexModel([('video_id', 1), ('reporter_id', 1)], unique=True, name='reports_open_unique',
                   partialFilterExpression={'status': 'open'}),
    ],
    'follows': [
        IndexModel([('follower_id', 1), ('followee_id', 1)], unique=True, name='follows_pair_unique'),
        IndexModel([('followee_id', 1), ('created_at', -1), ('_id', -1)], name='follows_followers'),
        IndexModel([('follower_id', 1), ('created_at', -1), ('_id', -1)], name='follows_following'),
    ],
    'blocks': [
        IndexModel([('user_id', 1), ('blocked_user_id', 1)], unique=True, name='blocks_pair_unique'),
        IndexModel([('user_id', 1), ('created_at', -1), ('_id', -1)], name='blocks_list'),
    ],
}


def ensure_indexes(database):
    try:
        for collection, models in INDEXES.items():
            database[collection].create_indexes(models)
    except PyMongoError:
        raise DataUnavailable() from None
