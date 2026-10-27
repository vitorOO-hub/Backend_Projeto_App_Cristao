import json

import pytest
from bson import ObjectId

from app.errors import ApiError
from app.purge import purge_pending
from world import MP4_BYTES

INTERNALS = ('storage_key', 'video_codec', 'purged_at', 'deleted_at', 'author_id', 'decided_by', 'password')


def captions(response):
    return [item['caption'] for item in response.json['data']]


def test_feed_lists_only_approved_newest_first(world):
    author, visitor = world.user(), world.visitor()
    first = world.video(author, caption='primeiro')
    world.video(author, status='pending', caption='pendente')
    world.video(author, status='rejected', caption='rejeitado')
    second = world.video(author, caption='segundo')
    gone = world.video(author, caption='excluído')
    world.videos.soft_delete(gone['_id'], author.id, world.time[0])
    response = visitor.get('/api/videos')
    assert response.status_code == 200
    assert captions(response) == ['segundo', 'primeiro']
    assert response.json['pagination'] == {'next_cursor': None, 'has_more': False}
    item = response.json['data'][0]
    assert item['id'] == str(second['_id']) and item['media_url'] == f"/api/videos/{second['_id']}/media"
    assert item['author']['name'] == 'Maria' and item['status'] == 'approved'
    assert (item['reaction_count'], item['comment_count'], item['reacted_by_me']) == (0, 0, False)
    assert response.headers['Cache-Control'] == 'no-store'
    assert not any(secret in response.text for secret in INTERNALS + ('review',))
    assert first['_id'] != second['_id']


def test_empty_feed_is_a_valid_empty_list(world):
    response = world.visitor().get('/api/videos')
    assert response.status_code == 200 and response.json['data'] == []


def test_feed_filters_by_category_and_author(world):
    maria, joao = world.user('Maria'), world.user('Joao')
    world.video(maria, category='louvor', caption='a')
    world.video(maria, category='humor', caption='b')
    world.video(joao, category='humor', caption='c')
    visitor = world.visitor()
    assert captions(visitor.get('/api/videos?category=humor')) == ['c', 'b']
    assert captions(visitor.get(f'/api/videos?author_id={maria.id}')) == ['b', 'a']
    assert captions(visitor.get(f'/api/videos?author_id={maria.id}&category=humor')) == ['b']


def test_feed_pagination_has_no_repeats_and_reports_the_end(world):
    author, visitor = world.user(), world.visitor()
    for n in range(5):
        world.video(author, caption=f'v{n}')
    seen, url = [], '/api/videos?limit=2'
    while True:
        response = visitor.get(url)
        seen += captions(response)
        pagination = response.json['pagination']
        if not pagination['has_more']:
            assert pagination['next_cursor'] is None
            break
        url = '/api/videos?limit=2&cursor=' + pagination['next_cursor']
    assert seen == ['v4', 'v3', 'v2', 'v1', 'v0']


def test_cursor_is_bound_to_its_filters(world):
    author, visitor = world.user(), world.visitor()
    for n in range(3):
        world.video(author, caption=f'v{n}', category='humor')
    cursor = visitor.get('/api/videos?limit=1&category=humor').json['pagination']['next_cursor']
    assert visitor.get(f'/api/videos?limit=1&category=humor&cursor={cursor}').status_code == 200
    assert visitor.get(f'/api/videos?limit=1&category=louvor&cursor={cursor}').status_code == 400
    assert visitor.get(f'/api/videos?limit=1&cursor={cursor}').status_code == 400


@pytest.mark.parametrize('query', [
    'limit=0', 'limit=31', 'limit=abc', 'limit=-1', 'limit=%EF%BC%91', 'limit=1&limit=2', 'limit=',
    'category=musica', 'category=louvor&category=humor', 'author_id=xyz', 'author_id=' + 'g' * 24,
    'cursor=invalid', 'cursor=' + 'A' * 500, 'unknown=1', 'status=approved'])
def test_feed_rejects_bad_parameters(world, query):
    response = world.visitor().get('/api/videos?' + query)
    assert response.status_code == 400
    assert response.json['error']['code'] == 'validation_error'


def test_feed_counts_and_my_reaction(world):
    author, fan, visitor = world.user('Autora'), world.user('Fa'), world.visitor()
    video = world.video(author)
    world.reactions.add(fan.id, video['_id'], world.time[0])
    world.reactions.add(author.id, video['_id'], world.time[0])
    world.comments.insert({'_id': ObjectId(), 'video_id': video['_id'], 'author_id': fan.id,
                           'text': 'Amém', 'created_at': world.time[0]})
    assert fan.get('/api/videos').json['data'][0]['reacted_by_me'] is True
    item = visitor.get('/api/videos').json['data'][0]
    assert (item['reaction_count'], item['comment_count'], item['reacted_by_me']) == (2, 1, False)


def test_feed_hides_authors_the_viewer_blocked(world):
    maria, joao, viewer = world.user('Maria'), world.user('Joao'), world.user('Viewer')
    world.video(maria, caption='da maria')
    world.video(joao, caption='do joao')
    viewer.put(f'/api/me/blocks/{maria.id}')
    assert captions(viewer.get('/api/videos')) == ['do joao']
    assert captions(world.visitor().get('/api/videos')) == ['do joao', 'da maria']


def test_feed_comment_count_ignores_blocked_commenters(world):
    author, viewer, troll = world.user('Autor'), world.user('Viewer'), world.user('Troll')
    video = world.video(author)
    for who in (author, troll):
        world.comments.insert({'_id': ObjectId(), 'video_id': video['_id'], 'author_id': who.id,
                               'text': 'oi', 'created_at': world.time[0]})
    viewer.put(f'/api/me/blocks/{troll.id}')
    assert viewer.get('/api/videos').json['data'][0]['comment_count'] == 1
    assert world.visitor().get('/api/videos').json['data'][0]['comment_count'] == 2


def test_feed_unavailable_database_is_503(world):
    world.videos.down = True
    response = world.visitor().get('/api/videos')
    assert response.status_code == 503 and response.json['error']['code'] == 'data_unavailable'


# --- detail ------------------------------------------------------------------------------------

def test_detail_visibility_by_state(world):
    author, other, admin, visitor = world.user('Autor'), world.user('Outro'), world.admin(), world.visitor()
    approved = world.video(author)
    pending = world.video(author, status='pending')
    rejected = world.video(author, status='rejected')
    public = visitor.get(f"/api/videos/{approved['_id']}")
    assert public.status_code == 200 and 'review_reason' not in public.json['data']
    for hidden in (pending, rejected):
        url = f"/api/videos/{hidden['_id']}"
        assert visitor.get(url).status_code == 404
        assert other.get(url).status_code == 404
        assert author.get(url).status_code == 200 and 'review_reason' in author.get(url).json['data']
        assert admin.get(url).status_code == 200
    assert rejected_reason(author, rejected) == 'Conteúdo adequado ao público.'
    assert not any(secret in public.text for secret in INTERNALS)


def rejected_reason(actor, video):
    return actor.get(f"/api/videos/{video['_id']}").json['data']['review_reason']


def test_detail_of_deleted_or_unknown_or_malformed(world):
    author = world.user()
    video = world.video(author)
    world.videos.soft_delete(video['_id'], author.id, world.time[0])
    assert author.get(f"/api/videos/{video['_id']}").status_code == 404
    assert author.get(f'/api/videos/{ObjectId()}').status_code == 404
    assert author.get('/api/videos/not-an-id').status_code == 400


def test_detail_hides_video_of_a_blocked_author_except_from_admin(world):
    author, viewer, admin = world.user('Autor'), world.user('Viewer'), world.admin()
    video = world.video(author)
    viewer.put(f'/api/me/blocks/{author.id}')
    admin.put(f'/api/me/blocks/{author.id}')
    url = f"/api/videos/{video['_id']}"
    assert viewer.get(url).status_code == 404
    assert world.visitor().get(url).status_code == 200
    assert admin.get(url).status_code == 200


# --- upload ------------------------------------------------------------------------------------

def test_upload_creates_a_pending_video_owned_by_the_session_user(world):
    author = world.user('Autora')
    response = author.upload(bible_reference=' João 3:16 ')
    assert response.status_code == 201
    data = response.json['data']
    assert response.headers['Location'] == f"/api/videos/{data['id']}"
    assert data['status'] == 'pending' and data['review_reason'] is None
    assert data['bible_reference'] == 'João 3:16' and data['duration_seconds'] == 42.5
    assert data['author']['id'] == str(author.id)
    assert not any(secret in response.text for secret in INTERNALS)
    stored = world.videos.videos[ObjectId(data['id'])]
    assert stored['author_id'] == author.id and stored['status'] == 'pending'
    assert stored['file']['bytes'] == len(MP4_BYTES) and stored['file']['video_codec'] == 'h264'
    assert world.storage.path(stored['file']['storage_key']).read_bytes() == MP4_BYTES
    assert list(world.storage.temp.iterdir()) == []
    assert len(world.probe_calls) == 1
    assert world.visitor().get('/api/videos').json['data'] == []
    assert [i['id'] for i in author.get('/api/me/videos').json['data']] == [data['id']]


def test_upload_requires_csrf_and_an_account(world):
    assert world.visitor().upload().status_code == 403
    assert world.anonymous_session().upload().status_code == 401
    user = world.user()
    from io import BytesIO
    forged = user.post('/api/videos', headers={'X-CSRF-Token': 'wrong'}, content_type='multipart/form-data',
                       data={'caption': 'Oi', 'category': 'louvor', 'file': (BytesIO(MP4_BYTES), 'a.mp4')})
    assert forged.status_code == 403
    assert not world.videos.videos and not world.probe_calls
    assert list(world.storage.temp.iterdir()) == []


@pytest.mark.parametrize('fields', [
    {'caption': ''}, {'caption': '   '}, {'caption': 'x' * 501}, {'category': 'musica'},
    {'bible_reference': 'x' * 101}, {'status': 'approved'}, {'author_id': str(ObjectId())},
    {'file_info': 'x'}, {'role': 'admin'}])
def test_upload_rejects_bad_metadata_before_touching_the_file(world, fields):
    user = world.user()
    assert user.upload(**fields).status_code == 400
    assert not world.probe_calls and not world.videos.videos
    assert list(world.storage.temp.iterdir()) == []


def test_upload_requires_caption_and_category(world):
    user = world.user()
    for missing in ('caption', 'category'):
        data = {'caption': 'Oi', 'category': 'louvor'}
        data.pop(missing)
        from io import BytesIO
        response = user.post('/api/videos', data={**data, 'file': (BytesIO(MP4_BYTES), 'a.mp4')},
                             content_type='multipart/form-data')
        assert response.status_code == 400


def test_upload_file_shape(world):
    user = world.user()
    assert user.upload(content=None).status_code == 400
    assert user.upload(filename='clip.mov').status_code == 415
    assert user.upload(filename='clip.MP4').status_code == 201
    from io import BytesIO
    two = user.post('/api/videos', data={'caption': 'Oi', 'category': 'louvor',
                                         'file': [(BytesIO(MP4_BYTES), 'a.mp4'), (BytesIO(MP4_BYTES), 'b.mp4')]},
                    content_type='multipart/form-data')
    assert two.status_code == 400
    other = user.post('/api/videos', data={'caption': 'Oi', 'category': 'louvor',
                                           'file': (BytesIO(MP4_BYTES), 'a.mp4'),
                                           'extra': (BytesIO(MP4_BYTES), 'b.mp4')},
                      content_type='multipart/form-data')
    assert other.status_code == 400
    assert user.post('/api/videos', json={'caption': 'Oi', 'category': 'louvor'}).status_code == 415
    assert len(world.videos.videos) == 1


def test_upload_larger_than_the_video_limit_is_413_and_leaves_nothing(world):
    world.app.config['MAX_VIDEO_BYTES'] = 100
    user = world.user()
    response = user.upload(content=MP4_BYTES)
    assert response.status_code == 413 and response.json['error']['code'] == 'file_too_large'
    assert not world.probe_calls and not world.videos.videos
    assert list(world.storage.temp.iterdir()) == []


def test_upload_request_larger_than_the_upload_limit_is_413(world):
    world.app.config['MAX_CONTENT_LENGTH'] = 2000
    response = world.user().upload(content=b'\x00\x00\x00\x18ftypmp42' + b'0' * 5000)
    assert response.status_code == 413
    assert not world.videos.videos and not world.probe_calls


def test_only_the_upload_route_may_exceed_the_json_limit(world):
    big = json.dumps({'text': 'x' * (17 * 1024)})
    video = world.video(world.user())
    commenter = world.user()
    assert commenter.post(f"/api/videos/{video['_id']}/comments", data=big,
                          content_type='application/json').status_code == 413


def test_upload_probe_rejection_cleans_up(world):
    world.probe_error = ApiError(422, 'invalid_media', 'inválido')
    response = world.user().upload()
    assert response.status_code == 422
    assert not world.videos.videos
    assert list(world.storage.temp.iterdir()) == [] and [p for p in world.storage.root.iterdir() if p.is_file()] == []


def test_upload_without_ffprobe_is_503_and_never_accepted(world):
    world.probe_error = ApiError(503, 'media_unavailable', 'indisponível')
    assert world.user().upload().status_code == 503
    assert not world.videos.videos


def test_upload_database_failure_removes_the_stored_file(world):
    world.videos.down = True
    assert world.user().upload().status_code == 503
    assert [p for p in world.storage.root.iterdir() if p.is_file()] == []
    assert list(world.storage.temp.iterdir()) == []


# --- own videos --------------------------------------------------------------------------------

def test_my_videos_shows_every_state_only_mine(world):
    me, other = world.user('Eu'), world.user('Outro')
    world.video(me, status='approved', caption='aprovado')
    world.video(me, status='pending', caption='pendente')
    world.video(me, status='rejected', caption='rejeitado')
    world.video(other, status='pending', caption='alheio')
    response = me.get('/api/me/videos')
    assert captions(response) == ['rejeitado', 'pendente', 'aprovado']
    assert all('review_reason' in item for item in response.json['data'])
    assert captions(me.get('/api/me/videos?status=pending')) == ['pendente']
    assert me.get('/api/me/videos?status=deleted').status_code == 400
    assert world.visitor().get('/api/me/videos').status_code == 401


def test_my_videos_paginates(world):
    me = world.user()
    for n in range(3):
        world.video(me, status='pending', caption=f'p{n}')
    first = me.get('/api/me/videos?limit=2')
    assert first.json['pagination']['has_more'] is True
    second = me.get('/api/me/videos?limit=2&cursor=' + first.json['pagination']['next_cursor'])
    assert captions(first) + captions(second) == ['p2', 'p1', 'p0']
    assert me.get('/api/me/videos?limit=2&status=pending&cursor=' + first.json['pagination']['next_cursor']).status_code == 400


# --- edit --------------------------------------------------------------------------------------

def test_author_edits_metadata_and_approved_goes_back_to_pending(world):
    author = world.user()
    video = world.video(author)
    world.advance(minutes=5)
    response = author.patch(f"/api/videos/{video['_id']}", json={'caption': '  Novo texto  ', 'category': 'humor'})
    assert response.status_code == 200
    data = response.json['data']
    assert data['caption'] == 'Novo texto' and data['category'] == 'humor'
    assert data['status'] == 'pending' and data['review_reason'] is None
    assert world.visitor().get('/api/videos').json['data'] == []
    assert data['updated_at'] > data['created_at']


def test_edit_keeps_pending_and_rejected_states(world):
    author = world.user()
    rejected = world.video(author, status='rejected')
    pending = world.video(author, status='pending')
    for video, status in ((rejected, 'rejected'), (pending, 'pending')):
        response = author.patch(f"/api/videos/{video['_id']}", json={'bible_reference': 'Salmos 23:1'})
        assert response.json['data']['status'] == status
        assert response.json['data']['bible_reference'] == 'Salmos 23:1'


def test_edit_can_clear_the_bible_reference(world):
    author = world.user()
    video = world.video(author, status='pending')
    author.patch(f"/api/videos/{video['_id']}", json={'bible_reference': 'João 3:16'})
    response = author.patch(f"/api/videos/{video['_id']}", json={'bible_reference': None})
    assert response.json['data']['bible_reference'] is None


@pytest.mark.parametrize('body', [
    {}, [], None, {'caption': ''}, {'category': 'x'}, {'status': 'approved'}, {'author_id': 'x'},
    {'file': {'storage_key': 'x'}}, {'review_reason': 'x'}, {'caption': 'ok', 'deleted_at': None}])
def test_edit_rejects_protected_or_invalid_fields(world, body):
    author = world.user()
    video = world.video(author, status='pending')
    response = author.patch(f"/api/videos/{video['_id']}", json=body)
    assert response.status_code == 400
    assert world.videos.videos[video['_id']]['caption'] == video['caption']


def test_edit_permissions(world):
    author, other = world.user('Autor'), world.user('Outro')
    approved = world.video(author)
    pending = world.video(author, status='pending')
    assert other.patch(f"/api/videos/{approved['_id']}", json={'caption': 'x'}).status_code == 403
    assert other.patch(f"/api/videos/{pending['_id']}", json={'caption': 'x'}).status_code == 404
    assert world.visitor().patch(f"/api/videos/{approved['_id']}", json={'caption': 'x'}).status_code == 403
    assert world.anonymous_session().patch(f"/api/videos/{approved['_id']}", json={'caption': 'x'}).status_code == 401
    assert world.videos.videos[approved['_id']]['caption'] == approved['caption']


# --- delete ------------------------------------------------------------------------------------

def test_author_deletes_and_everything_is_cleaned(world):
    author, fan = world.user('Autor'), world.user('Fa')
    video = world.video(author)
    world.reactions.add(fan.id, video['_id'], world.time[0])
    world.comments.insert({'_id': ObjectId(), 'video_id': video['_id'], 'author_id': fan.id,
                           'text': 'Amém', 'created_at': world.time[0]})
    path = world.storage.path(video['file']['storage_key'])
    assert path.exists()
    assert author.delete(f"/api/videos/{video['_id']}").status_code == 204
    assert not path.exists()
    assert not world.reactions.pairs and not world.comments.comments
    assert world.videos.videos[video['_id']]['purged_at'] is not None
    url = f"/api/videos/{video['_id']}"
    assert world.visitor().get(url).status_code == 404 and author.get(url).status_code == 404
    assert world.visitor().get(url + '/media').status_code == 404
    assert world.visitor().get('/api/videos').json['data'] == []
    assert author.delete(url).status_code == 404


def test_delete_permissions(world):
    author, other = world.user('Autor'), world.user('Outro')
    video = world.video(author)
    pending = world.video(author, status='pending')
    assert other.delete(f"/api/videos/{video['_id']}").status_code == 403
    assert other.delete(f"/api/videos/{pending['_id']}").status_code == 404
    assert world.visitor().delete(f"/api/videos/{video['_id']}").status_code == 403
    assert world.videos.videos[video['_id']]['deleted_at'] is None


def test_failed_physical_cleanup_does_not_fail_the_delete_and_is_resumable(world, monkeypatch):
    author = world.user()
    video = world.video(author)
    original = world.storage.delete

    def broken(key):
        raise OSError('disk busy')

    monkeypatch.setattr(world.storage, 'delete', broken)
    assert author.delete(f"/api/videos/{video['_id']}").status_code == 204
    assert world.videos.videos[video['_id']]['purged_at'] is None
    assert world.visitor().get(f"/api/videos/{video['_id']}").status_code == 404
    monkeypatch.setattr(world.storage, 'delete', original)
    with world.app.app_context():
        assert purge_pending() == 1
        assert purge_pending() == 0
    assert world.videos.videos[video['_id']]['purged_at'] is not None
    assert not world.storage.path(video['file']['storage_key']).exists()


# --- media -------------------------------------------------------------------------------------

def test_media_is_served_with_range_support_and_private_no_store(world):
    author, visitor = world.user(), world.visitor()
    video = world.video(author)
    url = f"/api/videos/{video['_id']}/media"
    full = visitor.get(url)
    assert full.status_code == 200 and full.data == MP4_BYTES
    assert full.mimetype == 'video/mp4' and full.headers['Accept-Ranges'] == 'bytes'
    assert full.headers['Cache-Control'] == 'private, no-store'
    part = visitor.get(url, headers={'Range': 'bytes=0-9'})
    assert part.status_code == 206 and part.data == MP4_BYTES[:10]
    assert part.headers['Content-Range'] == f'bytes 0-9/{len(MP4_BYTES)}'
    assert visitor.get(url, headers={'Range': 'bytes=99999-'}).status_code == 416


def test_media_follows_the_same_visibility_as_the_detail(world):
    author, other, admin, visitor = world.user('Autor'), world.user('Outro'), world.admin(), world.visitor()
    pending = world.video(author, status='pending')
    url = f"/api/videos/{pending['_id']}/media"
    assert visitor.get(url).status_code == 404 and other.get(url).status_code == 404
    assert author.get(url).status_code == 200 and admin.get(url).status_code == 200
    approved = world.video(author)
    other.put(f'/api/me/blocks/{author.id}')
    assert other.get(f"/api/videos/{approved['_id']}/media").status_code == 404


def test_media_missing_file_and_bad_id(world):
    author = world.user()
    video = world.video(author, with_file=False)
    assert world.visitor().get(f"/api/videos/{video['_id']}/media").status_code == 404
    assert world.visitor().get('/api/videos/zzz/media').status_code == 400


def test_media_has_its_own_larger_request_budget(world):
    video = world.video(world.user())
    visitor = world.visitor()
    statuses = {visitor.get(f"/api/videos/{video['_id']}/media", headers={'Range': 'bytes=0-1'}).status_code
                for _ in range(130)}
    assert statuses == {206}
