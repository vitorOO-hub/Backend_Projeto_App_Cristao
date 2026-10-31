"""Lifecycle and maintenance regressions with isolated HTTP fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.errors import ApiError, DataUnavailable, ReportConflict
from app.interactions import MongoInteractions
from app.social import new_comment, new_report

social_api = import_module('app.social_api')


@pytest.mark.parametrize('method,suffix,body', [
    ('put', 'reaction', None), ('delete', 'reaction', None),
    ('post', 'comments', {'text': 'Amém'}),
    ('post', 'reports', {'reason': 'Motivo válido para revisão'}),
])
def test_deletion_after_initial_authorization_cannot_create_an_orphan(world, monkeypatch,
                                                                   method, suffix, body):
    author, viewer = world.user('Autor'), world.user('Visitante')
    video = world.video(author)
    original = social_api.approved_video

    def stale_authorization(video_id, user):
        authorized = original(video_id, user)
        assert author.delete(f'/api/videos/{video_id}').status_code == 204
        return authorized

    monkeypatch.setattr(social_api, 'approved_video', stale_authorization)
    response = viewer.send(method, f'/api/videos/{video["_id"]}/{suffix}',
                           **({'json': body} if body else {}))
    assert response.status_code == 404
    assert not world.reactions.pairs and not world.comments.comments and not world.reports.reports
    assert world.videos.videos[video['_id']]['purged_at'] is not None


def test_failed_interaction_rolls_back_the_guard_and_insert(world, monkeypatch):
    author, viewer = world.user(), world.user()
    video = world.video(author)
    insert = world.comments.insert

    def fail_after_insert(document):
        insert(document)
        raise DataUnavailable()

    monkeypatch.setattr(world.comments, 'insert', fail_after_insert)
    response = viewer.post(f'/api/videos/{video["_id"]}/comments', json={'text': 'Amém'})
    assert response.status_code == 503
    assert not world.comments.comments
    assert 'interaction_version' not in world.videos.videos[video['_id']]


def test_orphan_diagnosis_is_read_only_and_does_not_print_content(world):
    author = world.user()
    live, deleted = world.video(author, status='pending'), world.video(author)
    world.videos.soft_delete(deleted['_id'], author.id, world.time[0])
    world.videos.mark_purged(deleted['_id'], world.time[0])
    for video_id in (live['_id'], deleted['_id'], ObjectId()):
        world.comments.insert(new_comment(video_id, author.id, {'text': 'private text'}, world.time[0]))
        world.reactions.add(author.id, video_id, world.time[0])
    report = new_report(deleted['_id'], author.id, {'reason': 'private report reason'}, world.time[0])
    world.reports.insert(report)
    before = deepcopy((world.videos.videos, world.comments.comments, world.reactions.pairs,
                       world.reports.reports))
    files = sorted(world.storage.root.glob('*.mp4'))
    result = world.app.test_cli_runner().invoke(args=['audit-orphans'])
    assert result.exit_code == 0, result.output
    assert 'comments: 2 órfãos nesta página.' in result.output
    assert 'reactions: 2 órfãos nesta página.' in result.output
    assert 'private' not in result.output
    assert str(live['_id']) not in result.output
    assert before == (world.videos.videos, world.comments.comments, world.reactions.pairs,
                      world.reports.reports)
    assert files == sorted(world.storage.root.glob('*.mp4'))


def test_explicit_recheck_cleans_previously_purged_videos_and_keeps_reports(world):
    author = world.user()
    videos = [world.video(author) for _ in range(3)]
    for video in videos:
        world.videos.soft_delete(video['_id'], author.id, world.time[0])
        world.videos.mark_purged(video['_id'], world.time[0])
        world.comments.insert(new_comment(video['_id'], author.id, {'text': 'legacy orphan'}, world.time[0]))
        world.reactions.add(author.id, video['_id'], world.time[0])
        world.reports.insert(new_report(video['_id'], author.id, {'reason': 'Keep audit history'}, world.time[0]))
    runner = world.app.test_cli_runner()
    assert 'Vídeos limpos: 0.' in runner.invoke(args=['purge-videos']).output
    first = runner.invoke(args=['purge-videos', '--recheck', '--limit', '2'])
    assert first.exit_code == 0, first.output
    assert 'Vídeos limpos: 2.' in first.output
    cursor = first.output.split('Próximo cursor: ')[1].strip()
    second = runner.invoke(args=['purge-videos', '--recheck', '--limit', '2', '--after', cursor])
    assert second.exit_code == 0 and 'Vídeos limpos: 1.' in second.output
    assert 'Próximo cursor' not in second.output
    assert not world.comments.comments and not world.reactions.pairs
    assert len(world.reports.reports) == 3
    assert not list(world.storage.root.glob('*.mp4'))


def test_orphan_diagnosis_continues_with_a_collection_specific_cursor(world):
    author = world.user()
    for _ in range(3):
        world.comments.insert(new_comment(ObjectId(), author.id, {'text': 'legacy orphan'}, world.time[0]))
    runner = world.app.test_cli_runner()
    first = runner.invoke(args=['audit-orphans', '--collection', 'comments', '--limit', '2'])
    assert first.exit_code == 0 and 'comments: 2 órfãos' in first.output
    assert 'reactions:' not in first.output
    cursor = first.output.split('Próximo cursor (comments): ')[1].strip()
    second = runner.invoke(args=['audit-orphans', '--collection', 'comments', '--limit', '2', '--after', cursor])
    assert second.exit_code == 0 and 'comments: 1 órfãos' in second.output
    assert 'Próximo cursor' not in second.output
    assert len(world.comments.comments) == 3


def test_recheck_failure_is_resumable(world, monkeypatch):
    author = world.user()
    video = world.video(author)
    world.videos.soft_delete(video['_id'], author.id, world.time[0])
    world.videos.mark_purged(video['_id'], world.time[0])
    comment = new_comment(video['_id'], author.id, {'text': 'legacy orphan'}, world.time[0])
    world.comments.insert(comment)
    remove = world.comments.delete_for_video
    def fail_cleanup(*_):
        raise DataUnavailable()

    monkeypatch.setattr(world.comments, 'delete_for_video', fail_cleanup)
    runner = world.app.test_cli_runner()
    assert runner.invoke(args=['purge-videos', '--recheck']).exit_code == 1
    assert comment['_id'] in world.comments.comments
    monkeypatch.setattr(world.comments, 'delete_for_video', remove)
    assert runner.invoke(args=['purge-videos', '--recheck']).exit_code == 0
    assert not world.comments.comments


@pytest.mark.parametrize('args', [
    ['audit-orphans', '--after', 'invalid'], ['audit-orphans', '--limit', '0'],
    ['purge-videos', '--after', '0' * 24], ['purge-videos', '--recheck', '--after', 'invalid'],
])
def test_maintenance_rejects_invalid_cursors_and_limits(world, args):
    result = world.app.test_cli_runner().invoke(args=args)
    assert result.exit_code != 0
    assert 'Traceback' not in result.output


class ImmediateTransaction:
    def run(self, operation):
        return operation('session')


def test_transaction_service_fails_closed_if_guard_cannot_write():
    database = {name: MagicMock() for name in ('videos', 'comments', 'reactions', 'reports')}
    database['videos'].update_one.return_value.matched_count = 0
    service = MongoInteractions(database, ImmediateTransaction())
    document = new_comment(ObjectId(), ObjectId(), {'text': 'Amém'}, datetime.now(timezone.utc))
    with pytest.raises(ApiError) as error:
        service.comment(document)
    assert error.value.status == 404
    database['comments'].insert_one.assert_not_called()


def test_transaction_database_errors_and_report_conflict_are_controlled():
    database = {name: MagicMock() for name in ('videos', 'comments', 'reactions', 'reports')}
    database['videos'].update_one.return_value.matched_count = 1
    service = MongoInteractions(database, ImmediateTransaction())
    database['comments'].insert_one.side_effect = OperationFailure('private database details')
    at = datetime.now(timezone.utc)
    with pytest.raises(DataUnavailable):
        service.comment(new_comment(ObjectId(), ObjectId(), {'text': 'Amém'}, at))
    database['reports'].insert_one.side_effect = DuplicateKeyError('private index details')
    with pytest.raises(ReportConflict):
        service.report(new_report(ObjectId(), ObjectId(), {'reason': 'Valid review reason'}, at))
