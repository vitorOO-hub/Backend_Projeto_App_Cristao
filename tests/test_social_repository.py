from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure, ServerSelectionTimeoutError

from app.errors import DataUnavailable, ReportConflict
from app.social_repository import (MongoBlockRepository, MongoCommentRepository,
                                   MongoReactionRepository, MongoReportRepository,
                                   UnavailableBlockRepository, UnavailableCommentRepository,
                                   UnavailableReactionRepository, UnavailableReportRepository)
from app.video_repository import NEWEST_FIRST, OLDEST_FIRST

AT = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
MILLIS = AT.replace(microsecond=123000)
USER, OTHER, VIDEO, VIDEO2, ADMIN, ITEM = (ObjectId() for _ in range(6))


def make(cls, name):
    return cls({name: MagicMock()})


@pytest.fixture
def reactions():
    return make(MongoReactionRepository, 'reactions')


@pytest.fixture
def comments():
    return make(MongoCommentRepository, 'comments')


@pytest.fixture
def reports():
    return make(MongoReportRepository, 'reports')


@pytest.fixture
def blocks():
    return make(MongoBlockRepository, 'blocks')


def sorted_chain(collection, items):
    chain = collection.find.return_value.sort.return_value.limit
    chain.return_value = items
    return chain


# --- reactions ---------------------------------------------------------------------------------

def test_reaction_add_is_an_idempotent_upsert(reactions):
    reactions.add(USER, VIDEO, AT)
    reactions.reactions.update_one.assert_called_once_with(
        {'user_id': USER, 'video_id': VIDEO}, {'$setOnInsert': {'created_at': MILLIS}}, upsert=True)


def test_reaction_race_on_the_unique_index_is_not_an_error(reactions):
    reactions.reactions.update_one.side_effect = DuplicateKeyError('duplicate')
    reactions.add(USER, VIDEO, AT)


def test_reaction_remove_is_idempotent(reactions):
    reactions.remove(USER, VIDEO)
    reactions.reactions.delete_one.assert_called_once_with({'user_id': USER, 'video_id': VIDEO})


def test_reaction_counts_use_one_aggregation_per_page(reactions):
    reactions.reactions.aggregate.return_value = [{'_id': VIDEO, 'total': 3}]
    assert reactions.count_for([VIDEO, VIDEO2]) == {VIDEO: 3}
    reactions.reactions.aggregate.assert_called_once_with([
        {'$match': {'video_id': {'$in': [VIDEO, VIDEO2]}}},
        {'$group': {'_id': '$video_id', 'total': {'$sum': 1}}}])
    reactions.reactions.aggregate.reset_mock()
    assert reactions.count_for([]) == {}
    reactions.reactions.aggregate.assert_not_called()


def test_reacted_by_returns_only_this_users_videos(reactions):
    reactions.reactions.find.return_value = [{'video_id': VIDEO}]
    assert reactions.reacted_by(USER, [VIDEO, VIDEO2]) == {VIDEO}
    reactions.reactions.find.assert_called_once_with(
        {'user_id': USER, 'video_id': {'$in': [VIDEO, VIDEO2]}}, {'video_id': 1})
    assert reactions.reacted_by(USER, []) == set()


def test_reactions_purge_by_video(reactions):
    reactions.reactions.delete_many.return_value.deleted_count = 4
    assert reactions.delete_for_video(VIDEO) == 4
    reactions.reactions.delete_many.assert_called_once_with({'video_id': VIDEO})


# --- comments ----------------------------------------------------------------------------------

def test_comment_insert_and_find(comments):
    document = {'_id': ITEM}
    assert comments.insert(document) == document
    comments.comments.insert_one.assert_called_once_with(document)
    comments.find_by_id(ITEM)
    comments.comments.find_one.assert_called_once_with({'_id': ITEM})


def test_comment_list_is_oldest_first_and_skips_blocked_authors_before_the_limit(comments):
    limit = sorted_chain(comments.comments, [{'n': 1}])
    assert comments.list_for_video(VIDEO, exclude_authors=[OTHER], limit=5) == ([{'n': 1}], False)
    comments.comments.find.assert_called_once_with(
        {'video_id': VIDEO, 'author_id': {'$nin': [OTHER]}})
    comments.comments.find.return_value.sort.assert_called_once_with(OLDEST_FIRST)
    limit.assert_called_once_with(6)
    comments.list_for_video(VIDEO)
    assert comments.comments.find.call_args.args[0] == {'video_id': VIDEO}


def test_comment_count_respects_blocked_authors(comments):
    comments.comments.aggregate.return_value = [{'_id': VIDEO, 'total': 2}]
    assert comments.count_for([VIDEO], exclude_authors=[OTHER]) == {VIDEO: 2}
    match = comments.comments.aggregate.call_args.args[0][0]['$match']
    assert match == {'author_id': {'$nin': [OTHER]}, 'video_id': {'$in': [VIDEO]}}


def test_only_the_author_can_delete_a_comment(comments):
    comments.comments.delete_one.return_value.deleted_count = 1
    assert comments.delete(ITEM, USER)
    comments.comments.delete_one.assert_called_once_with({'_id': ITEM, 'author_id': USER})
    comments.comments.delete_one.return_value.deleted_count = 0
    assert not comments.delete(ITEM, OTHER)


def test_comments_purge_by_video(comments):
    comments.comments.delete_many.return_value.deleted_count = 2
    assert comments.delete_for_video(VIDEO) == 2
    comments.comments.delete_many.assert_called_once_with({'video_id': VIDEO})


# --- reports -----------------------------------------------------------------------------------

def test_report_insert_and_duplicate_open_report(reports):
    document = {'_id': ITEM}
    assert reports.insert(document) == document
    reports.reports.insert_one.side_effect = DuplicateKeyError('duplicate')
    with pytest.raises(ReportConflict):
        reports.insert(document)


def test_report_queue_is_oldest_first_and_validates_status(reports):
    sorted_chain(reports.reports, [])
    reports.list_by_status()
    reports.reports.find.assert_called_once_with({'status': 'open'})
    reports.reports.find.return_value.sort.assert_called_once_with(OLDEST_FIRST)
    for status in ('deleted', {'$ne': 'x'}, None):
        with pytest.raises(ValueError):
            reports.list_by_status(status=status)


def test_resolve_only_open_reports(reports):
    reports.resolve(ITEM, note='Revisado e mantido', admin_id=ADMIN, at=AT)
    args, kwargs = reports.reports.find_one_and_update.call_args
    assert args[0] == {'_id': ITEM, 'status': 'open'}
    assert args[1] == {'$set': {'status': 'resolved', 'resolution_note': 'Revisado e mantido',
                                'resolved_by': ADMIN, 'resolved_at': MILLIS}}
    assert kwargs == {'return_document': ReturnDocument.AFTER}
    with pytest.raises(ValueError):
        reports.resolve(ITEM, note=None, admin_id=ADMIN, at=AT)
    with pytest.raises(ValueError):
        reports.resolve(ITEM, note='Revisado e mantido', admin_id=str(ADMIN), at=AT)


# --- blocks ------------------------------------------------------------------------------------

def test_block_add_remove_are_idempotent(blocks):
    blocks.add(USER, OTHER, AT)
    blocks.blocks.update_one.assert_called_once_with(
        {'user_id': USER, 'blocked_user_id': OTHER}, {'$setOnInsert': {'created_at': MILLIS}}, upsert=True)
    blocks.blocks.update_one.side_effect = DuplicateKeyError('duplicate')
    blocks.add(USER, OTHER, AT)
    blocks.remove(USER, OTHER)
    blocks.blocks.delete_one.assert_called_once_with({'user_id': USER, 'blocked_user_id': OTHER})


def test_block_listing_and_lookups(blocks):
    sorted_chain(blocks.blocks, [])
    blocks.list_for(USER)
    blocks.blocks.find.assert_called_once_with({'user_id': USER})
    blocks.blocks.find.return_value.sort.assert_called_once_with(NEWEST_FIRST)
    blocks.blocks.find.reset_mock()
    blocks.blocks.find.return_value.limit.return_value = [{'blocked_user_id': OTHER}]
    assert blocks.blocked_ids(USER) == [OTHER]
    blocks.blocks.find.assert_called_once_with({'user_id': USER}, {'blocked_user_id': 1})
    blocks.blocks.count_documents.return_value = 1
    assert blocks.exists(USER, OTHER)
    blocks.blocks.count_documents.return_value = 0
    assert not blocks.exists(USER, OTHER)


# --- shared guarantees -------------------------------------------------------------------------

@pytest.mark.parametrize('call', [
    lambda r, c, rp, b: r.add(str(USER), VIDEO, AT),
    lambda r, c, rp, b: r.count_for([str(VIDEO)]),
    lambda r, c, rp, b: r.count_for([ObjectId()] * 101),
    lambda r, c, rp, b: r.reacted_by({'$ne': None}, [VIDEO]),
    lambda r, c, rp, b: c.find_by_id({'$ne': None}),
    lambda r, c, rp, b: c.delete(ITEM, {'$ne': None}),
    lambda r, c, rp, b: c.list_for_video(VIDEO, exclude_authors=[{'$ne': None}]),
    lambda r, c, rp, b: c.list_for_video(VIDEO, limit=31),
    lambda r, c, rp, b: rp.find_by_id('x'),
    lambda r, c, rp, b: b.add(USER, str(OTHER), AT),
    lambda r, c, rp, b: b.list_for(USER, after=(MILLIS, str(ITEM))),
    lambda r, c, rp, b: b.exists({'$ne': None}, OTHER)])
def test_client_values_are_never_used_as_filters(reactions, comments, reports, blocks, call):
    with pytest.raises(ValueError):
        call(reactions, comments, reports, blocks)
    for repository, name in ((reactions, 'reactions'), (comments, 'comments'), (reports, 'reports'), (blocks, 'blocks')):
        collection = getattr(repository, name)
        assert not (collection.find.called or collection.update_one.called or collection.delete_one.called
                    or collection.aggregate.called or collection.count_documents.called)


@pytest.mark.parametrize('error', [ServerSelectionTimeoutError('private uri'), OperationFailure('private')])
def test_database_errors_are_controlled(reactions, comments, reports, blocks, error):
    for repository, name in ((reactions, 'reactions'), (comments, 'comments'), (reports, 'reports'), (blocks, 'blocks')):
        collection = getattr(repository, name)
        for operation in ('update_one', 'delete_one', 'delete_many', 'insert_one', 'find_one', 'find',
                          'aggregate', 'find_one_and_update', 'count_documents'):
            getattr(collection, operation).side_effect = error
    calls = [lambda: reactions.add(USER, VIDEO, AT), lambda: reactions.remove(USER, VIDEO),
             lambda: reactions.count_for([VIDEO]), lambda: reactions.reacted_by(USER, [VIDEO]),
             lambda: reactions.delete_for_video(VIDEO), lambda: comments.insert({}),
             lambda: comments.find_by_id(ITEM), lambda: comments.list_for_video(VIDEO),
             lambda: comments.count_for([VIDEO]), lambda: comments.delete(ITEM, USER),
             lambda: comments.delete_for_video(VIDEO), lambda: reports.insert({}),
             lambda: reports.find_by_id(ITEM), lambda: reports.list_by_status(),
             lambda: reports.resolve(ITEM, note='Revisado e mantido', admin_id=ADMIN, at=AT),
             lambda: blocks.add(USER, OTHER, AT), lambda: blocks.remove(USER, OTHER),
             lambda: blocks.list_for(USER), lambda: blocks.blocked_ids(USER), lambda: blocks.exists(USER, OTHER)]
    for call in calls:
        with pytest.raises(DataUnavailable) as raised:
            call()
        assert 'private' not in str(raised.value)


def test_offline_repositories_never_fall_back():
    offline = [(UnavailableReactionRepository(), 'add'), (UnavailableCommentRepository(), 'insert'),
               (UnavailableReportRepository(), 'resolve'), (UnavailableBlockRepository(), 'exists')]
    for repository, method in offline:
        with pytest.raises(DataUnavailable):
            getattr(repository, method)(USER, note='x')


def test_every_repository_is_wired_and_fails_closed_without_database(app):
    for name in ('video', 'reaction', 'comment', 'report', 'block'):
        assert f'{name}_repository' in app.extensions
    with pytest.raises(DataUnavailable):
        app.extensions['block_repository'].exists(USER, OTHER)
    with pytest.raises(DataUnavailable):
        app.extensions['comment_repository'].delete(ITEM, USER)
