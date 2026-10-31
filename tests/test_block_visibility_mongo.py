"""Opt-in discovery regressions using more than a thousand blocks in a new local database."""
import os
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.db import ensure_indexes
from app.interactions import MongoInteractions
from app.social import new_comment
from app.social_repository import (MongoBlockRepository, MongoCommentRepository, MongoFollowRepository,
                                   MongoReactionRepository, MongoReportRepository)
from app.video_repository import MongoVideoRepository
from world import World


@pytest.fixture
def blocked_world(tmp_path):
    uri = os.getenv('TEST_MONGODB_URI')
    if not uri:
        pytest.skip('TEST_MONGODB_URI is required for isolated local MongoDB tests.')
    parts = urlsplit(uri)
    if parts.scheme != 'mongodb' or parts.hostname not in ('localhost', '127.0.0.1', '::1'):
        pytest.fail('The disposable MongoDB test requires a loopback address.')
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                         socketTimeoutMS=10000, tz_aware=True)
    name = 'app_cristao_security_test_' + uuid4().hex
    try:
        client.admin.command('ping')
        database = client[name]
        ensure_indexes(database)
        world = World(tmp_path)
        for key, repository in (
            ('video', MongoVideoRepository), ('comment', MongoCommentRepository),
            ('reaction', MongoReactionRepository), ('block', MongoBlockRepository),
            ('follow', MongoFollowRepository), ('report', MongoReportRepository),
        ):
            instance = repository(database)
            setattr(world, key + 's', instance)
            world.app.extensions[key + '_repository'] = instance
        world.app.extensions['interactions_service'] = MongoInteractions(database)
        yield world, database
    finally:
        client.drop_database(name)
        client.close()


def seed_blocks(world, database, viewer, hidden):
    blocked = [ObjectId() for _ in range(1000)] + [hidden.id]
    database.blocks.insert_many([
        {'user_id': viewer.id, 'blocked_user_id': user_id, 'created_at': world.time[0]}
        for user_id in blocked
    ])
    return blocked


def response_ids(response):
    assert response.status_code == 200, response.json
    return [item['id'] for item in response.json['data']]


def test_thousandth_block_is_applied_before_feed_pages_and_direct_media(blocked_world):
    world, database = blocked_world
    viewer, hidden, kept = world.user('Viewer'), world.user('Hidden'), world.user('Kept')
    old, recent = world.video(kept), world.video(kept)
    invisible = [world.video(hidden) for _ in range(3)]
    seed_blocks(world, database, viewer, hidden)

    first = viewer.get('/api/videos?limit=1')
    assert response_ids(first) == [str(recent['_id'])]
    second = viewer.get('/api/videos?limit=1&cursor=' + first.json['pagination']['next_cursor'])
    assert response_ids(second) == [str(old['_id'])]
    assert second.json['pagination'] == {'next_cursor': None, 'has_more': False}
    assert viewer.get(f'/api/videos/{invisible[0]["_id"]}').status_code == 404
    assert viewer.get(f'/api/videos/{invisible[0]["_id"]}/media').status_code == 404
    assert response_ids(viewer.get(f'/api/videos?author_id={hidden.id}')) == []
    assert len(response_ids(world.visitor().get(f'/api/videos?author_id={hidden.id}'))) == 3
    assert viewer.delete(f'/api/me/blocks/{hidden.id}').status_code == 204
    assert viewer.get(f'/api/videos/{invisible[0]["_id"]}').status_code == 200


def test_comment_pages_counts_and_detail_consider_every_block(blocked_world):
    world, database = blocked_world
    viewer, hidden, kept = world.user('Viewer'), world.user('Hidden'), world.user('Kept')
    video = world.video(kept)
    blocked = seed_blocks(world, database, viewer, hidden)
    at = world.time[0]
    hidden_comments = [new_comment(video['_id'], user_id, {'text': 'Hidden comment'},
                                   at + timedelta(milliseconds=n)) for n, user_id in enumerate(blocked)]
    shown = [new_comment(video['_id'], kept.id, {'text': f'Shown {n}'},
                         at + timedelta(seconds=2 + n)) for n in range(2)]
    database.comments.insert_many(hidden_comments + shown)
    database.reactions.insert_one({'user_id': hidden.id, 'video_id': video['_id'], 'created_at': at})

    first = viewer.get(f'/api/videos/{video["_id"]}/comments?limit=1')
    assert response_ids(first) == [str(shown[0]['_id'])]
    second = viewer.get(f'/api/videos/{video["_id"]}/comments?limit=1&cursor=' +
                        first.json['pagination']['next_cursor'])
    assert response_ids(second) == [str(shown[1]['_id'])]
    assert not second.json['pagination']['has_more']
    detail = viewer.get(f'/api/videos/{video["_id"]}').json['data']
    assert detail['comment_count'] == 2 and detail['reaction_count'] == 1
    assert viewer.get(f'/api/comments/{hidden_comments[-1]["_id"]}').status_code == 404
    assert world.visitor().get(f'/api/comments/{hidden_comments[-1]["_id"]}').status_code == 200


def test_profile_follow_and_reaction_pages_filter_before_limit_and_count(blocked_world):
    world, database = blocked_world
    viewer, hidden, first, second = (world.user(name) for name in ('Viewer', 'Hidden', 'First', 'Second'))
    blocked = seed_blocks(world, database, viewer, hidden)
    old, recent, not_seen = world.video(first), world.video(second), world.video(hidden)
    rejected = world.video(first, status='rejected')
    deleted = world.video(first)
    database.videos.update_one({'_id': deleted['_id']}, {'$set': {'deleted_at': world.time[0]}})
    at = world.time[0]
    database.reactions.insert_many([
        {'user_id': viewer.id, 'video_id': video['_id'], 'created_at': at + timedelta(seconds=n)}
        for n, video in enumerate((old, recent, not_seen, rejected, deleted))
    ])
    for user_id in blocked + [first.id, second.id]:
        database.follows.insert_many([
            {'follower_id': viewer.id, 'followee_id': user_id, 'created_at': at},
            {'follower_id': user_id, 'followee_id': viewer.id, 'created_at': at},
        ])
    first_page = viewer.get('/api/me/reacted-videos?limit=1')
    assert response_ids(first_page) == [str(recent['_id'])]
    next_page = viewer.get('/api/me/reacted-videos?limit=1&cursor=' +
                           first_page.json['pagination']['next_cursor'])
    assert response_ids(next_page) == [str(old['_id'])]
    assert not next_page.json['pagination']['has_more']
    for path in ('followers', 'following'):
        page = viewer.get(f'/api/me/{path}?limit=1')
        continuation = viewer.get(f'/api/me/{path}?limit=1&cursor=' + page.json['pagination']['next_cursor'])
        assert set(response_ids(page) + response_ids(continuation)) == {str(first.id), str(second.id)}
        assert not continuation.json['pagination']['has_more']
    counts = viewer.get('/api/me/profile').json['data']['counts']
    assert counts['followers'] == counts['following'] == counts['reacted'] == 2


def test_admin_blocked_discovery_is_filtered_but_moderation_keeps_access(blocked_world):
    world, database = blocked_world
    admin, hidden = world.admin(), world.user('Hidden')
    public, pending = world.video(hidden), world.video(hidden, status='pending')
    seed_blocks(world, database, admin, hidden)
    assert response_ids(admin.get('/api/videos')) == []
    assert admin.get(f'/api/videos/{public["_id"]}').status_code == 200
    assert admin.get(f'/api/videos/{pending["_id"]}/media').status_code == 200
    assert response_ids(admin.get('/api/moderation/videos')) == [str(pending['_id'])]
