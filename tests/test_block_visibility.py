"""Block filtering keeps discovery, counters and detail consistent without enumerating blocks."""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

from app.social import new_comment
from app.social_repository import MongoCommentRepository, MongoFollowRepository, MongoReactionRepository
from app.video_repository import MongoVideoRepository


def ids(response):
    assert response.status_code == 200, response.json
    return [item['id'] for item in response.json['data']]


def block_after_thousand(world, viewer, hidden):
    for blocked in [ObjectId() for _ in range(1000)] + [hidden.id]:
        world.blocks.add(viewer.id, blocked, world.time[0])

    def forbidden_materialization(*_args, **_kwargs):
        raise AssertionError('Discovery must not load a complete block list.')

    # A former API, deliberately poisoned so a future regression cannot reintroduce it.
    world.blocks.blocked_ids = forbidden_materialization


def test_discovery_filters_all_blocks_before_paginating_and_counting(world):
    viewer, hidden, first, second = (world.user(name) for name in ('Viewer', 'Hidden', 'First', 'Second'))
    old, recent = world.video(first), world.video(second)
    hidden_video = world.video(hidden)
    block_after_thousand(world, viewer, hidden)
    at = world.time[0]
    for n, video in enumerate((old, recent, hidden_video)):
        world.reactions.add(viewer.id, video['_id'], at + timedelta(seconds=n))
    for n, person in enumerate((first, second, hidden)):
        world.follows.add(viewer.id, person.id, at + timedelta(seconds=n))
        world.follows.add(person.id, viewer.id, at + timedelta(seconds=n))
    for n, person in enumerate((hidden, first, second)):
        world.comments.insert(new_comment(recent['_id'], person.id, {'text': person.user['name']},
                                           at + timedelta(seconds=n)))

    first_page = viewer.get('/api/videos?limit=1')
    assert ids(first_page) == [str(recent['_id'])]
    next_page = viewer.get('/api/videos?limit=1&cursor=' + first_page.json['pagination']['next_cursor'])
    assert ids(next_page) == [str(old['_id'])]
    assert not next_page.json['pagination']['has_more']
    assert first_page.json['data'][0]['comment_count'] == 2
    assert viewer.get(f'/api/videos/{hidden_video["_id"]}').status_code == 404
    assert viewer.get(f'/api/videos/{hidden_video["_id"]}/media').status_code == 404

    comments = viewer.get(f'/api/videos/{recent["_id"]}/comments?limit=1')
    assert [item['author']['id'] for item in comments.json['data']] == [str(first.id)]
    comments_next = viewer.get(f'/api/videos/{recent["_id"]}/comments?limit=1&cursor=' +
                               comments.json['pagination']['next_cursor'])
    assert [item['author']['id'] for item in comments_next.json['data']] == [str(second.id)]

    assert ids(viewer.get('/api/me/reacted-videos?limit=1')) == [str(recent['_id'])]
    assert ids(viewer.get('/api/me/following?limit=1')) == [str(second.id)]
    assert ids(viewer.get('/api/me/followers?limit=1')) == [str(second.id)]
    counts = viewer.get('/api/me/profile').json['data']['counts']
    assert counts['reacted'] == counts['followers'] == counts['following'] == 2


def test_blocking_is_unilateral_and_admin_discovery_remains_filtered(world):
    viewer, hidden, admin = world.user('Viewer'), world.user('Hidden'), world.admin()
    mine, theirs = world.video(viewer), world.video(hidden)
    pending = world.video(hidden, status='pending')
    block_after_thousand(world, viewer, hidden)
    block_after_thousand(world, admin, hidden)
    assert ids(hidden.get('/api/videos')) == [str(theirs['_id']), str(mine['_id'])]
    assert str(theirs['_id']) in ids(world.visitor().get('/api/videos'))
    assert str(theirs['_id']) not in ids(admin.get('/api/videos'))
    assert admin.get(f'/api/videos/{theirs["_id"]}').status_code == 200
    assert admin.get(f'/api/videos/{pending["_id"]}/media').status_code == 200
    assert ids(viewer.get(f'/api/videos?author_id={hidden.id}')) == []
    assert viewer.delete(f'/api/me/blocks/{hidden.id}').status_code == 204
    assert viewer.get(f'/api/videos/{theirs["_id"]}').status_code == 200


@pytest.mark.parametrize('operation', [
    lambda videos, comments, follows, reactions: videos.list_approved(viewer_id={'$ne': None}),
    lambda videos, comments, follows, reactions: videos.find_approved_by_ids([ObjectId()], viewer_id='bad'),
    lambda videos, comments, follows, reactions: comments.list_for_video(ObjectId(), viewer_id=['bad']),
    lambda videos, comments, follows, reactions: comments.count_for([ObjectId()], viewer_id={'$ne': None}),
    lambda videos, comments, follows, reactions: follows.list_followers(ObjectId(), viewer_id='bad'),
    lambda videos, comments, follows, reactions: follows.count_following(ObjectId(), viewer_id='bad'),
    lambda videos, comments, follows, reactions: reactions.list_by_user(ObjectId(), viewer_id='bad'),
    lambda videos, comments, follows, reactions: reactions.count_by_user(ObjectId(), viewer_id='bad'),
])
def test_viewer_identity_is_validated_before_any_driver_query(operation):
    database = {name: MagicMock() for name in ('videos', 'comments', 'follows', 'reactions')}
    with pytest.raises(ValueError):
        operation(MongoVideoRepository(database), MongoCommentRepository(database),
                  MongoFollowRepository(database), MongoReactionRepository(database))
    assert all(not collection.mock_calls for collection in database.values())
