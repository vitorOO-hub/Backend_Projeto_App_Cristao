from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.errors import ApiError
from app.social import (block_public, check_block, comment_public, new_comment, new_report,
                        report_public, validate_resolution)

AT = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
VIDEO, USER = ObjectId(), ObjectId()


def author():
    return {'_id': USER, 'name': 'Maria', 'bio': '', 'created_at': AT, 'role': 'user'}


def test_comment_is_built_by_the_server():
    comment = new_comment(VIDEO, USER, {'text': '  Amém!  '}, AT)
    assert comment['text'] == 'Amém!'
    assert comment['video_id'] == VIDEO and comment['author_id'] == USER
    assert comment['created_at'].microsecond == 123000
    assert set(comment) == {'_id', 'video_id', 'author_id', 'text', 'created_at'}


@pytest.mark.parametrize('payload', [
    None, [], {}, {'text': ''}, {'text': '   '}, {'text': 'x' * 1001}, {'text': 5}, {'text': {'$ne': 1}},
    {'text': 'a', 'author_id': 'x'}, {'text': 'a', 'video_id': 'x'}, {'txt': 'a'}, {'text': 'a' + chr(0xd800)}])
def test_invalid_comment(payload):
    with pytest.raises(ApiError) as error:
        new_comment(VIDEO, USER, payload, AT)
    assert error.value.status == 400


def test_comment_boundaries_and_untrusted_ids():
    assert new_comment(VIDEO, USER, {'text': 'x'}, AT)
    assert len(new_comment(VIDEO, USER, {'text': 'x' * 1000}, AT)['text']) == 1000
    for ids in ((str(VIDEO), USER), (VIDEO, str(USER)), (None, USER)):
        with pytest.raises(ValueError):
            new_comment(*ids, {'text': 'a'}, AT)


def test_report_starts_open_without_resolution():
    report = new_report(VIDEO, USER, {'reason': ' Conteúdo ofensivo ao público '}, AT)
    assert report['status'] == 'open' and report['reason'] == 'Conteúdo ofensivo ao público'
    assert report['resolution_note'] is None and report['resolved_by'] is None and report['resolved_at'] is None


@pytest.mark.parametrize('reason', ['curto', 'x' * 1001, '         ', '', None, 5])
def test_invalid_report_reason(reason):
    with pytest.raises(ApiError):
        new_report(VIDEO, USER, {'reason': reason}, AT)


def test_report_reason_boundaries():
    assert new_report(VIDEO, USER, {'reason': 'x' * 10}, AT)
    assert new_report(VIDEO, USER, {'reason': 'x' * 1000}, AT)
    with pytest.raises(ApiError):
        new_report(VIDEO, USER, {'reason': 'x' * 9}, AT)


def test_resolution_payload():
    assert validate_resolution({'status': 'resolved', 'resolution_note': ' Revisado e mantido '}) == 'Revisado e mantido'
    for payload in (None, {}, {'status': 'resolved'}, {'resolution_note': 'Revisado e mantido'},
                    {'status': 'open', 'resolution_note': 'Revisado e mantido'},
                    {'status': 'resolved', 'resolution_note': 'curta'},
                    {'status': 'resolved', 'resolution_note': 'Revisado e mantido', 'resolved_by': 'x'}):
        with pytest.raises(ApiError):
            validate_resolution(payload)


def test_cannot_block_yourself():
    check_block(USER, ObjectId())
    with pytest.raises(ApiError) as error:
        check_block(USER, USER)
    assert error.value.status == 400
    with pytest.raises(ValueError):
        check_block(str(USER), ObjectId())


def test_comment_json():
    comment = new_comment(VIDEO, USER, {'text': 'Glória a Deus'}, AT)
    data = comment_public(comment, author())
    assert data['id'] == str(comment['_id']) and data['video_id'] == str(VIDEO)
    assert data['text'] == 'Glória a Deus' and data['author']['id'] == str(USER)
    assert set(data) == {'id', 'video_id', 'author', 'text', 'created_at'}
    assert 'email' not in repr(data) and 'role' not in repr(data) and 'author_id' not in repr(data)


def test_report_json_hides_who_resolved_it():
    report = new_report(VIDEO, USER, {'reason': 'Conteúdo ofensivo ao público'}, AT)
    assert report_public(report)['resolved_at'] is None
    report.update(status='resolved', resolved_by=ObjectId(), resolved_at=AT, resolution_note='Mantido')
    data = report_public(report)
    assert data['resolved_at'] == '2026-10-27T10:00:00.123456Z' and data['reporter_id'] == str(USER)
    assert set(data) == {'id', 'video_id', 'reporter_id', 'reason', 'status', 'resolution_note',
                         'created_at', 'resolved_at'}
    assert 'resolved_by' not in data


def test_block_json():
    data = block_public({'created_at': AT}, author())
    assert set(data) == {'user', 'created_at'} and data['user']['name'] == 'Maria'
    assert 'email' not in repr(data)
