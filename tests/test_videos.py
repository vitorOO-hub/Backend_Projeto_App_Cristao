from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.errors import ApiError
from app.videos import (EMPTY_REVIEW, can_view, new_video, to_millis, validate_file_info,
                        validate_metadata, video_public, video_restricted)

AT = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
FILE = {'storage_key': 'a' * 32 + '.mp4', 'bytes': 1234, 'duration_seconds': 42.5,
        'video_codec': 'h264', 'audio_codec': 'aac', 'width': 1080, 'height': 1920}


def author():
    return {'_id': ObjectId(), 'name': 'Maria', 'bio': '', 'created_at': AT, 'role': 'user'}


def video(**changes):
    return {**new_video(ObjectId(), {'caption': 'Louvor', 'category': 'louvor'}, FILE, AT), **changes}


def test_millisecond_truncation_matches_bson_precision():
    assert to_millis(AT).microsecond == 123000
    assert to_millis(AT.replace(microsecond=0)).microsecond == 0


def test_metadata_is_trimmed_and_optional_reference_normalized():
    clean = validate_metadata({'caption': '  Olá  ', 'category': 'humor', 'bible_reference': '  '})
    assert clean == {'caption': 'Olá', 'category': 'humor', 'bible_reference': None}
    assert validate_metadata({'caption': 'a', 'category': 'louvor'}) == {'caption': 'a', 'category': 'louvor'}
    assert validate_metadata({'bible_reference': ' João 3:16 '}, partial=True) == {'bible_reference': 'João 3:16'}
    assert validate_metadata({'bible_reference': None}, partial=True) == {'bible_reference': None}


@pytest.mark.parametrize('payload', [
    None, [], {}, {'caption': 'a'}, {'category': 'louvor'},
    {'caption': '  ', 'category': 'louvor'}, {'caption': 'x' * 501, 'category': 'louvor'},
    {'caption': 'a', 'category': 'musica'}, {'caption': 'a', 'category': ['louvor']},
    {'caption': 'a', 'category': 'louvor', 'bible_reference': 'x' * 101},
    {'caption': 'a', 'category': 'louvor', 'bible_reference': 5},
    {'caption': 'a', 'category': 'louvor', 'status': 'approved'},
    {'caption': 'a', 'category': 'louvor', 'author_id': 'x'},
    {'caption': 'a', 'category': 'louvor', 'file': {}},
    {'caption': {'$ne': None}, 'category': 'louvor'}, {'caption': 'a' + chr(0xd800), 'category': 'louvor'}])
def test_invalid_metadata(payload):
    with pytest.raises(ApiError) as error:
        validate_metadata(payload)
    assert error.value.status == 400


@pytest.mark.parametrize('payload', [{}, {'status': 'approved'}, {'caption': ''}, {'category': 'x'}])
def test_invalid_partial_metadata(payload):
    with pytest.raises(ApiError):
        validate_metadata(payload, partial=True)


def test_caption_boundaries():
    assert validate_metadata({'caption': 'a', 'category': 'louvor'})['caption'] == 'a'
    assert len(validate_metadata({'caption': 'x' * 500, 'category': 'louvor'})['caption']) == 500
    assert validate_metadata({'caption': 'a', 'category': 'louvor', 'bible_reference': 'x' * 100})


def test_new_video_is_pending_and_server_controlled():
    created = new_video(ObjectId(), {'caption': 'Oi', 'category': 'testemunho'}, FILE, AT)
    assert created['status'] == 'pending'
    assert created['bible_reference'] is None
    assert created['review'] == EMPTY_REVIEW and created['review'] is not EMPTY_REVIEW
    assert created['deleted_at'] is None and created['purged_at'] is None
    assert created['created_at'] == created['updated_at'] == to_millis(AT)
    assert created['file'] == FILE
    assert isinstance(created['_id'], ObjectId)


def test_new_video_rejects_client_fields_and_untrusted_author():
    with pytest.raises(ApiError):
        new_video(ObjectId(), {'caption': 'a', 'category': 'louvor', 'status': 'approved'}, FILE, AT)
    with pytest.raises(ValueError):
        new_video(str(ObjectId()), {'caption': 'a', 'category': 'louvor'}, FILE, AT)


@pytest.mark.parametrize('changes', [
    {'storage_key': '../../etc/passwd'}, {'storage_key': 'a' * 32 + '.mov'}, {'storage_key': 'A' * 32 + '.mp4'},
    {'storage_key': '/srv/uploads/' + 'a' * 32 + '.mp4'}, {'bytes': 0}, {'bytes': '10'},
    {'duration_seconds': 0}, {'duration_seconds': 90.01}, {'duration_seconds': '5'}, {'video_codec': None}])
def test_file_info_is_validated(changes):
    with pytest.raises(ValueError):
        validate_file_info({**FILE, **changes})


def test_file_info_requires_exact_fields():
    with pytest.raises(ValueError):
        validate_file_info({**FILE, 'path': '/srv/x.mp4'})
    with pytest.raises(ValueError):
        validate_file_info({key: value for key, value in FILE.items() if key != 'width'})
    assert validate_file_info({**FILE, 'audio_codec': None, 'duration_seconds': 90})['duration_seconds'] == 90


def test_can_view_rules():
    owner, other, admin = author(), author(), {**author(), 'role': 'admin'}
    pending = video(author_id=owner['_id'])
    assert not can_view(pending, None)
    assert not can_view(pending, other)
    assert can_view(pending, owner) and can_view(pending, admin)
    rejected = {**pending, 'status': 'rejected'}
    assert not can_view(rejected, other) and can_view(rejected, owner)
    approved = {**pending, 'status': 'approved'}
    assert can_view(approved, None) and can_view(approved, other)


def test_deleted_video_is_invisible_to_everyone():
    owner, admin = author(), {**author(), 'role': 'admin'}
    gone = video(author_id=owner['_id'], status='approved', deleted_at=AT)
    assert not any(can_view(gone, viewer) for viewer in (None, owner, admin))


def test_public_json_hides_internals():
    owner = author()
    approved = video(author_id=owner['_id'], status='approved')
    approved['review'] = {'reason': 'Nota interna da moderação', 'decided_by': ObjectId(), 'decided_at': AT}
    data = video_public(approved, owner, reaction_count=3, comment_count=2, reacted_by_me=True)
    assert data['id'] == str(approved['_id'])
    assert data['author'] == {'id': str(owner['_id']), 'name': 'Maria', 'bio': '', 'created_at': '2026-10-27T10:00:00.123456Z'}
    assert data['media_url'] == f"/api/videos/{approved['_id']}/media"
    assert (data['reaction_count'], data['comment_count'], data['reacted_by_me']) == (3, 2, True)
    assert data['duration_seconds'] == 42.5
    assert data['bible_reference'] is None
    text = repr(data)
    for secret in ('storage_key', 'h264', 'review', 'Nota interna', 'purged_at', 'deleted_at', 'author_id', FILE['storage_key']):
        assert secret not in text
    assert 'review_reason' not in data


def test_restricted_json_adds_only_the_review_reason():
    owner = author()
    rejected = video(author_id=owner['_id'], status='rejected')
    rejected['review'] = {'reason': 'Conteúdo fora das diretrizes', 'decided_by': ObjectId(), 'decided_at': AT}
    data = video_restricted(rejected, owner)
    assert data['review_reason'] == 'Conteúdo fora das diretrizes'
    assert {key: value for key, value in data.items() if key != 'review_reason'} == video_public(rejected, owner)
    assert 'decided_by' not in repr(data) and FILE['storage_key'] not in repr(data)
