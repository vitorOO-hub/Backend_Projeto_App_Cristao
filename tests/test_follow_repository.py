"""Mongo adapters behind the profile: follows, a user's reactions and the profile counters."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

from app.db import INDEXES
from app.errors import DataUnavailable
from app.social_repository import (MongoFollowRepository, MongoReactionRepository,
                                   UnavailableFollowRepository, UnavailableReactionRepository)
from app.video_repository import NEWEST_FIRST, MongoVideoRepository, UnavailableVideoRepository

AT = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
MILLIS = AT.replace(microsecond=123000)
ANA, BIA, CAIO, VIDEO = (ObjectId() for _ in range(4))


@pytest.fixture
def follows():
    return MongoFollowRepository({'follows': MagicMock()})


@pytest.fixture
def reactions():
    return MongoReactionRepository({'reactions': MagicMock(), 'videos': MagicMock()})


@pytest.fixture
def videos():
    return MongoVideoRepository({'videos': MagicMock()})


def index(collection, name):
    return next(model.document for model in INDEXES[collection] if model.document['name'] == name)


# --- follows -----------------------------------------------------------------------------------

def test_follow_is_an_upsert_so_repeating_it_never_duplicates(follows):
    follows.add(ANA, BIA, AT)
    follows.follows.update_one.assert_called_once_with(
        {'follower_id': ANA, 'followee_id': BIA}, {'$setOnInsert': {'created_at': MILLIS}}, upsert=True)


def test_a_racing_duplicate_follow_is_swallowed(follows):
    follows.follows.update_one.side_effect = DuplicateKeyError('dup')
    follows.add(ANA, BIA, AT)


def test_unfollow_deletes_only_that_pair(follows):
    follows.remove(ANA, BIA)
    follows.follows.delete_one.assert_called_once_with({'follower_id': ANA, 'followee_id': BIA})


def test_followers_are_found_by_followee_and_blocked_users_go_before_the_limit(follows):
    follows.follows.find.return_value.sort.return_value.limit.return_value = []
    follows.list_followers(ANA, exclude_users=[BIA], limit=5)
    query = follows.follows.find.call_args.args[0]
    assert query == {'followee_id': ANA, 'follower_id': {'$nin': [BIA]}}
    follows.follows.find.return_value.sort.assert_called_once_with(NEWEST_FIRST)
    follows.follows.find.return_value.sort.return_value.limit.assert_called_once_with(6)


def test_following_is_found_by_follower(follows):
    follows.follows.find.return_value.sort.return_value.limit.return_value = []
    follows.list_following(ANA, limit=5)
    assert follows.follows.find.call_args.args[0] == {'follower_id': ANA}


def test_counts_use_the_same_filters_as_the_lists(follows):
    follows.follows.count_documents.return_value = 3
    assert follows.count_followers(ANA, exclude_users=[BIA]) == 3
    follows.follows.count_documents.assert_called_with({'followee_id': ANA, 'follower_id': {'$nin': [BIA]}})
    assert follows.count_following(ANA) == 3
    follows.follows.count_documents.assert_called_with({'follower_id': ANA})


@pytest.mark.parametrize('call', [
    lambda r: r.add('x', BIA, AT), lambda r: r.add(ANA, {'$ne': None}, AT),
    lambda r: r.remove({'$gt': ''}, BIA), lambda r: r.list_followers('x'),
    lambda r: r.list_following({'$ne': None}), lambda r: r.count_followers('x'),
    lambda r: r.count_following(ANA, exclude_users=['x']),
])
def test_identifiers_must_be_objectids_never_client_operators(follows, call):
    with pytest.raises(ValueError):
        call(follows)
    follows.follows.update_one.assert_not_called()
    follows.follows.find.assert_not_called()
    follows.follows.count_documents.assert_not_called()


def test_database_errors_become_data_unavailable(follows):
    for collection_call in ('update_one', 'delete_one', 'count_documents', 'find'):
        getattr(follows.follows, collection_call).side_effect = ServerSelectionTimeoutError('down')
    for call in (lambda: follows.add(ANA, BIA, AT), lambda: follows.remove(ANA, BIA),
                 lambda: follows.count_followers(ANA), lambda: follows.list_followers(ANA)):
        with pytest.raises(DataUnavailable):
            call()


def test_offline_follow_repository_fails_closed():
    offline = UnavailableFollowRepository()
    for call in (lambda: offline.add(ANA, BIA, AT), lambda: offline.list_followers(ANA),
                 lambda: offline.count_following(ANA)):
        with pytest.raises(DataUnavailable):
            call()


# --- a user's reactions and the profile counters -----------------------------------------------

def test_reactions_of_a_user_are_listed_newest_first(reactions):
    reactions.reactions.find.return_value.sort.return_value.limit.return_value = []
    reactions.list_by_user(ANA, limit=10)
    assert reactions.reactions.find.call_args.args[0] == {'user_id': ANA}
    reactions.reactions.find.return_value.sort.assert_called_once_with(NEWEST_FIRST)


def test_reaction_count_by_user(reactions):
    reactions.reactions.count_documents.return_value = 4
    assert reactions.count_by_user(ANA) == 4
    reactions.reactions.count_documents.assert_called_once_with({'user_id': ANA})


def test_received_amens_are_summed_in_one_aggregation_over_the_authors_visible_videos(reactions):
    reactions.videos.aggregate.return_value = [{'_id': None, 'total': 7}]
    assert reactions.count_received(ANA) == 7
    (pipeline,), _ = reactions.videos.aggregate.call_args
    assert pipeline[0] == {'$match': {'status': 'approved', 'deleted_at': None, 'author_id': {'$eq': ANA}}}
    assert pipeline[1]['$lookup']['from'] == 'reactions'
    reactions.reactions.aggregate.assert_not_called()


def test_received_amens_are_zero_when_the_author_has_no_videos(reactions):
    reactions.videos.aggregate.return_value = []
    assert reactions.count_received(ANA) == 0


def test_profile_counters_reject_non_objectids_and_report_outages(reactions):
    for call in (lambda: reactions.count_received('x'), lambda: reactions.count_by_user({'$ne': None}),
                 lambda: reactions.list_by_user('x')):
        with pytest.raises(ValueError):
            call()
    reactions.videos.aggregate.side_effect = ServerSelectionTimeoutError('down')
    reactions.reactions.count_documents.side_effect = ServerSelectionTimeoutError('down')
    for call in (lambda: reactions.count_received(ANA), lambda: reactions.count_by_user(ANA)):
        with pytest.raises(DataUnavailable):
            call()


def test_offline_reaction_repository_fails_closed_for_the_profile_too():
    offline = UnavailableReactionRepository()
    for call in (lambda: offline.list_by_user(ANA), lambda: offline.count_by_user(ANA),
                 lambda: offline.count_received(ANA)):
        with pytest.raises(DataUnavailable):
            call()


# --- videos by id and published count ----------------------------------------------------------

def test_videos_by_id_use_the_public_feed_rules(videos):
    videos.videos.find.return_value = []
    videos.find_approved_by_ids([VIDEO], exclude_authors=[BIA])
    assert videos.videos.find.call_args.args[0] == {
        'status': 'approved', 'deleted_at': None, 'author_id': {'$nin': [BIA]}, '_id': {'$in': [VIDEO]}}


def test_videos_by_id_skip_the_database_for_an_empty_page_and_cap_the_batch(videos):
    assert videos.find_approved_by_ids([]) == []
    videos.videos.find.assert_not_called()
    with pytest.raises(ValueError):
        videos.find_approved_by_ids([ObjectId() for _ in range(31)])
    with pytest.raises(ValueError):
        videos.find_approved_by_ids(['x'])


def test_published_count_is_approved_and_undeleted_only(videos):
    videos.videos.count_documents.return_value = 2
    assert videos.count_approved_by_author(ANA) == 2
    videos.videos.count_documents.assert_called_once_with(
        {'status': 'approved', 'deleted_at': None, 'author_id': {'$eq': ANA}})
    with pytest.raises(ValueError):
        videos.count_approved_by_author('x')


def test_offline_video_repository_fails_closed_for_the_profile_too():
    offline = UnavailableVideoRepository()
    for call in (lambda: offline.find_approved_by_ids([VIDEO]), lambda: offline.count_approved_by_author(ANA)):
        with pytest.raises(DataUnavailable):
            call()


# --- indexes -----------------------------------------------------------------------------------

def test_follow_indexes_make_pairs_unique_and_serve_both_lists():
    assert index('follows', 'follows_pair_unique')['unique'] is True
    assert list(index('follows', 'follows_pair_unique')['key'].items()) == [('follower_id', 1), ('followee_id', 1)]
    assert list(index('follows', 'follows_followers')['key'].items()) == [
        ('followee_id', 1), ('created_at', -1), ('_id', -1)]
    assert list(index('follows', 'follows_following')['key'].items()) == [
        ('follower_id', 1), ('created_at', -1), ('_id', -1)]


def test_reactions_have_an_index_for_a_users_recent_reactions():
    assert list(index('reactions', 'reactions_user')['key'].items()) == [
        ('user_id', 1), ('created_at', -1), ('_id', -1)]
