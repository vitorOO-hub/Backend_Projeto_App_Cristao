from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from app.errors import DataUnavailable
from app.video_repository import (NEWEST_FIRST, OLDEST_FIRST, MongoVideoRepository,
                                  UnavailableVideoRepository, approved_query, cursor_clause)
from app.videos import EMPTY_REVIEW

AT = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
MILLIS = AT.replace(microsecond=123000)
VIDEO, AUTHOR, ADMIN = ObjectId(), ObjectId(), ObjectId()


@pytest.fixture
def repository():
    return MongoVideoRepository({'videos': MagicMock()})


def cursor(repository, items):
    chain = repository.videos.find.return_value.sort.return_value.limit
    chain.return_value = items
    return chain


def test_insert_and_find_hide_deleted(repository):
    document = {'_id': VIDEO}
    assert repository.insert(document) == document
    repository.videos.insert_one.assert_called_once_with(document)
    repository.find_by_id(VIDEO)
    repository.videos.find_one.assert_called_once_with({'_id': VIDEO, 'deleted_at': None})


def test_feed_query_is_approved_not_deleted_newest_first(repository):
    limit = cursor(repository, [{'n': 1}, {'n': 2}])
    items, more = repository.list_approved(limit=10)
    assert (items, more) == ([{'n': 1}, {'n': 2}], False)
    repository.videos.find.assert_called_once_with({'status': 'approved', 'deleted_at': None})
    repository.videos.find.return_value.sort.assert_called_once_with(NEWEST_FIRST)
    limit.assert_called_once_with(11)


def test_page_reports_more_with_one_extra_item(repository):
    cursor(repository, [{'n': n} for n in range(4)])
    items, more = repository.list_approved(limit=3)
    assert [item['n'] for item in items] == [0, 1, 2] and more is True


def test_feed_filters_category_author_and_blocked_authors():
    blocked = [ObjectId(), ObjectId()]
    query = approved_query('louvor', AUTHOR, blocked)
    assert query == {'status': 'approved', 'deleted_at': None, 'category': 'louvor',
                     'author_id': {'$eq': AUTHOR, '$nin': blocked}}
    assert approved_query(exclude_authors=blocked)['author_id'] == {'$nin': blocked}


@pytest.mark.parametrize('kwargs', [
    {'category': 'musica'}, {'category': {'$ne': None}}, {'author_id': {'$ne': None}},
    {'author_id': ['a']}, {'exclude_authors': [{'$ne': None}]}])
def test_feed_rejects_non_scalar_or_unknown_filters(kwargs):
    with pytest.raises(ValueError):
        approved_query(**kwargs)


def test_cursor_is_keyset_and_direction_aware(repository):
    after = (MILLIS, VIDEO)
    assert cursor_clause(after, True) == {'$or': [{'created_at': {'$lt': MILLIS}},
                                                  {'created_at': MILLIS, '_id': {'$lt': VIDEO}}]}
    assert cursor_clause(after, False)['$or'][0] == {'created_at': {'$gt': MILLIS}}
    cursor(repository, [])
    repository.list_approved(after=after)
    query = repository.videos.find.call_args.args[0]
    assert query['$and'][0]['status'] == 'approved' and query['$and'][1] == cursor_clause(after, True)


@pytest.mark.parametrize('after', [(MILLIS,), (str(MILLIS), VIDEO), (MILLIS, str(VIDEO)), [MILLIS, VIDEO], 'x', {'$gt': 1}])
def test_invalid_cursor(repository, after):
    with pytest.raises(ValueError):
        repository.list_approved(after=after)
    repository.videos.find.assert_not_called()


@pytest.mark.parametrize('limit', [0, 31, -1, '10', True, None, 1.5])
def test_invalid_page_limit(repository, limit):
    with pytest.raises(ValueError):
        repository.list_approved(limit=limit)


def test_author_listing_includes_every_state_but_not_deleted(repository):
    cursor(repository, [])
    repository.list_by_author(AUTHOR)
    repository.videos.find.assert_called_with({'author_id': AUTHOR, 'deleted_at': None})
    repository.list_by_author(AUTHOR, status='rejected')
    repository.videos.find.assert_called_with({'author_id': AUTHOR, 'deleted_at': None, 'status': 'rejected'})
    with pytest.raises(ValueError):
        repository.list_by_author(AUTHOR, status={'$ne': 'x'})
    with pytest.raises(ValueError):
        repository.list_by_author({'$ne': None})


def test_review_queue_is_oldest_first(repository):
    cursor(repository, [])
    repository.list_for_review()
    repository.videos.find.assert_called_once_with({'status': 'pending', 'deleted_at': None})
    repository.videos.find.return_value.sort.assert_called_once_with(OLDEST_FIRST)
    with pytest.raises(ValueError):
        repository.list_for_review(status='deleted')


def test_update_metadata_is_one_atomic_pipeline(repository):
    repository.update_metadata(VIDEO, AUTHOR, {'caption': '$secret', 'bible_reference': None}, AT)
    args, kwargs = repository.videos.find_one_and_update.call_args
    assert args[0] == {'_id': VIDEO, 'author_id': AUTHOR, 'deleted_at': None}
    assert kwargs == {'return_document': ReturnDocument.AFTER}
    (stage,) = args[1]
    fields = stage['$set']
    assert fields['caption'] == {'$literal': '$secret'}
    assert fields['bible_reference'] == {'$literal': None}
    assert fields['updated_at'] == {'$literal': MILLIS}
    approved = {'$eq': ['$status', 'approved']}
    assert fields['status'] == {'$cond': [approved, 'pending', '$status']}
    assert fields['review'] == {'$cond': [approved, {'$literal': EMPTY_REVIEW}, '$review']}


@pytest.mark.parametrize('fields', [{}, {'status': 'approved'}, {'author_id': AUTHOR}, {'file': {}}, None, []])
def test_update_metadata_rejects_protected_fields(repository, fields):
    with pytest.raises(ValueError):
        repository.update_metadata(VIDEO, AUTHOR, fields, AT)
    repository.videos.find_one_and_update.assert_not_called()


def test_decide_is_conditional_on_updated_at_and_transition(repository):
    repository.decide(VIDEO, expected_updated_at=MILLIS, status='approved',
                      reason='Conteúdo adequado', admin_id=ADMIN, at=AT)
    args, kwargs = repository.videos.find_one_and_update.call_args
    assert args[0] == {'_id': VIDEO, 'deleted_at': None, 'updated_at': MILLIS,
                       'status': {'$in': ['pending', 'rejected']}}
    assert args[1] == {'$set': {'status': 'approved', 'updated_at': MILLIS,
                                'review': {'reason': 'Conteúdo adequado', 'decided_by': ADMIN, 'decided_at': MILLIS}}}
    assert kwargs == {'return_document': ReturnDocument.AFTER}
    repository.decide(VIDEO, expected_updated_at=MILLIS, status='rejected',
                      reason='Fora das diretrizes', admin_id=ADMIN, at=AT)
    assert repository.videos.find_one_and_update.call_args.args[0]['status'] == {'$in': ['pending', 'approved']}


@pytest.mark.parametrize('changes', [
    {'status': 'pending'}, {'status': {'$ne': 'x'}}, {'reason': None}, {'expected_updated_at': '2026-10-27'},
    {'admin_id': 'admin'}])
def test_decide_rejects_invalid_input(repository, changes):
    values = dict(expected_updated_at=MILLIS, status='approved', reason='Conteúdo adequado', admin_id=ADMIN, at=AT)
    with pytest.raises((ValueError, TypeError)):
        repository.decide(VIDEO, **{**values, **changes})
    repository.videos.find_one_and_update.assert_not_called()


def test_soft_delete_requires_author_and_returns_outcome(repository):
    repository.videos.update_one.return_value.matched_count = 1
    assert repository.soft_delete(VIDEO, AUTHOR, AT)
    repository.videos.update_one.assert_called_once_with(
        {'_id': VIDEO, 'author_id': AUTHOR, 'deleted_at': None},
        {'$set': {'deleted_at': MILLIS, 'updated_at': MILLIS}})
    repository.videos.update_one.return_value.matched_count = 0
    assert not repository.soft_delete(VIDEO, AUTHOR, AT)


def test_purge_is_resumable_and_idempotent(repository):
    chain = repository.videos.find.return_value.sort.return_value.limit
    chain.return_value = [{'_id': VIDEO}]
    assert repository.list_pending_purge(20) == [{'_id': VIDEO}]
    repository.videos.find.assert_called_once_with({'deleted_at': {'$type': 'date'}, 'purged_at': None})
    chain.assert_called_once_with(20)
    repository.videos.update_one.return_value.matched_count = 1
    assert repository.mark_purged(VIDEO, AT)
    repository.videos.update_one.assert_called_once_with(
        {'_id': VIDEO, 'deleted_at': {'$type': 'date'}, 'purged_at': None}, {'$set': {'purged_at': MILLIS}})
    repository.videos.update_one.return_value.matched_count = 0
    assert not repository.mark_purged(VIDEO, AT)
    with pytest.raises(ValueError):
        repository.list_pending_purge(0)


@pytest.mark.parametrize('method, operation, call', [
    ('insert', 'insert_one', lambda r: r.insert({})),
    ('find_by_id', 'find_one', lambda r: r.find_by_id(VIDEO)),
    ('update_metadata', 'find_one_and_update', lambda r: r.update_metadata(VIDEO, AUTHOR, {'caption': 'a'}, AT)),
    ('decide', 'find_one_and_update', lambda r: r.decide(VIDEO, expected_updated_at=MILLIS, status='approved',
                                                          reason='Conteúdo adequado', admin_id=ADMIN, at=AT)),
    ('soft_delete', 'update_one', lambda r: r.soft_delete(VIDEO, AUTHOR, AT)),
    ('mark_purged', 'update_one', lambda r: r.mark_purged(VIDEO, AT))])
@pytest.mark.parametrize('error', [ServerSelectionTimeoutError('private uri'), OperationFailure('private')])
def test_mongo_errors_are_controlled(repository, method, operation, call, error):
    getattr(repository.videos, operation).side_effect = error
    with pytest.raises(DataUnavailable):
        call(repository)


@pytest.mark.parametrize('call', [
    lambda r: r.list_approved(), lambda r: r.list_by_author(AUTHOR), lambda r: r.list_for_review(),
    lambda r: r.list_pending_purge()])
def test_listing_errors_are_controlled(repository, call):
    repository.videos.find.side_effect = ServerSelectionTimeoutError('private uri')
    with pytest.raises(DataUnavailable):
        call(repository)


def test_unavailable_repository_never_falls_back():
    repository = UnavailableVideoRepository()
    for call in (lambda: repository.insert({}), lambda: repository.find_by_id(VIDEO),
                 lambda: repository.list_approved(limit=5), lambda: repository.decide(VIDEO, status='approved')):
        with pytest.raises(DataUnavailable):
            call()
