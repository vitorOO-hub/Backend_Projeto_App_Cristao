import pytest
from bson import ObjectId


def texts(response):
    return [item['text'] for item in response.json['data']]


def comment_on(actor, video, text='Glória a Deus'):
    return actor.post(f"/api/videos/{video['_id']}/comments", json={'text': text})


# --- reactions ---------------------------------------------------------------------------------

def test_reaction_is_idempotent_and_can_be_undone(world):
    author, fan, other = world.user('Autor'), world.user('Fa'), world.user('Outro')
    video = world.video(author)
    url = f"/api/videos/{video['_id']}/reaction"
    first = fan.put(url)
    assert first.status_code == 200 and first.json['data'] == {'reacted': True, 'reaction_count': 1}
    assert fan.put(url).json['data']['reaction_count'] == 1
    assert other.put(url).json['data']['reaction_count'] == 2
    assert fan.get('/api/videos').json['data'][0]['reacted_by_me'] is True
    assert fan.delete(url).status_code == 204
    assert fan.delete(url).status_code == 204
    assert world.visitor().get('/api/videos').json['data'][0]['reaction_count'] == 1


def test_reaction_needs_login_csrf_and_a_visible_approved_video(world):
    author, other = world.user('Autor'), world.user('Outro')
    approved = world.video(author)
    pending = world.video(author, status='pending')
    rejected = world.video(author, status='rejected')
    url = f"/api/videos/{approved['_id']}/reaction"
    assert world.visitor().put(url).status_code == 403
    assert world.anonymous_session().put(url).status_code == 401
    assert other.put(url, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    for hidden in (pending, rejected):
        for actor in (author, other):
            assert actor.put(f"/api/videos/{hidden['_id']}/reaction").status_code == 404
    assert other.put(f'/api/videos/{ObjectId()}/reaction').status_code == 404
    assert other.put('/api/videos/zzz/reaction').status_code == 400
    assert not world.reactions.pairs


def test_cannot_react_to_a_blocked_authors_video(world):
    author, viewer = world.user('Autor'), world.user('Viewer')
    video = world.video(author)
    viewer.put(f'/api/me/blocks/{author.id}')
    assert viewer.put(f"/api/videos/{video['_id']}/reaction").status_code == 404


# --- comments ----------------------------------------------------------------------------------

def test_comment_lifecycle(world):
    author, fan = world.user('Autor'), world.user('Fa')
    video = world.video(author)
    created = comment_on(fan, video, '  Amém!  ')
    assert created.status_code == 201
    data = created.json['data']
    assert data['text'] == 'Amém!' and data['video_id'] == str(video['_id'])
    assert data['author'] == {key: fan.user[key] if key != 'id' else str(fan.id) for key in ('id', 'name', 'bio')} | {
        'created_at': data['author']['created_at']}
    assert created.headers['Location'] == f"/api/comments/{data['id']}"
    assert 'email' not in created.text and 'password' not in created.text
    world.advance(seconds=5)
    comment_on(author, video, 'Obrigado!')
    listing = world.visitor().get(f"/api/videos/{video['_id']}/comments")
    assert listing.status_code == 200 and texts(listing) == ['Amém!', 'Obrigado!']
    assert listing.json['pagination'] == {'next_cursor': None, 'has_more': False}
    assert world.visitor().get(f"/api/comments/{data['id']}").json['data']['text'] == 'Amém!'
    assert fan.delete(f"/api/comments/{data['id']}").status_code == 204
    assert texts(world.visitor().get(f"/api/videos/{video['_id']}/comments")) == ['Obrigado!']
    assert world.visitor().get(f"/api/comments/{data['id']}").status_code == 404


@pytest.mark.parametrize('body', [
    None, [], {}, {'text': ''}, {'text': '   '}, {'text': 'x' * 1001}, {'text': 5}, {'text': {'$ne': 1}},
    {'text': 'ok', 'author_id': 'x'}, {'body': 'ok'}])
def test_invalid_comment_is_400(world, body):
    fan, video = world.user('Fa'), world.video(world.user('Autor'))
    response = fan.post(f"/api/videos/{video['_id']}/comments", json=body)
    assert response.status_code == 400 and not world.comments.comments


def test_comment_length_boundaries(world):
    fan, video = world.user('Fa'), world.video(world.user('Autor'))
    assert comment_on(fan, video, 'x').status_code == 201
    assert comment_on(fan, video, 'x' * 1000).status_code == 201


def test_comments_need_login_csrf_and_an_approved_visible_video(world):
    author, fan = world.user('Autor'), world.user('Fa')
    approved = world.video(author)
    pending = world.video(author, status='pending')
    assert world.visitor().post(f"/api/videos/{approved['_id']}/comments", json={'text': 'oi'}).status_code == 403
    assert world.anonymous_session().post(f"/api/videos/{approved['_id']}/comments",
                                          json={'text': 'oi'}).status_code == 401
    for actor in (author, fan):
        assert comment_on(actor, pending).status_code == 404
    assert world.visitor().get(f"/api/videos/{pending['_id']}/comments").status_code == 404
    assert author.get(f"/api/videos/{pending['_id']}/comments").status_code == 404
    assert not world.comments.comments


def test_comments_are_chronological_and_paginated(world):
    author, fan = world.user('Autor'), world.user('Fa')
    video = world.video(author)
    for n in range(5):
        world.advance(seconds=1)
        comment_on(fan, video, f'c{n}')
    url = f"/api/videos/{video['_id']}/comments"
    first = world.visitor().get(url + '?limit=2')
    assert texts(first) == ['c0', 'c1'] and first.json['pagination']['has_more'] is True
    second = world.visitor().get(url + '?limit=2&cursor=' + first.json['pagination']['next_cursor'])
    third = world.visitor().get(url + '?limit=2&cursor=' + second.json['pagination']['next_cursor'])
    assert texts(second) + texts(third) == ['c2', 'c3', 'c4'] and third.json['pagination']['has_more'] is False
    other_video = world.video(author)
    assert world.visitor().get(f"/api/videos/{other_video['_id']}/comments?cursor=" +
                               first.json['pagination']['next_cursor']).status_code == 400
    assert world.visitor().get(url + '?limit=99').status_code == 400


def test_blocked_authors_comments_are_hidden_everywhere_for_the_blocker(world):
    author, viewer, troll = world.user('Autor'), world.user('Viewer'), world.user('Troll')
    video = world.video(author)
    comment_on(author, video, 'bom')
    bad = comment_on(troll, video, 'ruim').json['data']['id']
    viewer.put(f'/api/me/blocks/{troll.id}')
    url = f"/api/videos/{video['_id']}/comments"
    assert texts(viewer.get(url)) == ['bom']
    assert viewer.get(f'/api/comments/{bad}').status_code == 404
    assert sorted(texts(world.visitor().get(url))) == ['bom', 'ruim']
    assert world.visitor().get(f'/api/comments/{bad}').status_code == 200
    viewer.delete(f'/api/me/blocks/{troll.id}')
    assert sorted(texts(viewer.get(url))) == ['bom', 'ruim']


def test_comment_deletion_permissions(world):
    author, fan, other = world.user('Autor'), world.user('Fa'), world.user('Outro')
    video = world.video(author)
    mine = comment_on(fan, video).json['data']['id']
    assert other.delete(f'/api/comments/{mine}').status_code == 403
    assert author.delete(f'/api/comments/{mine}').status_code == 403
    assert world.visitor().delete(f'/api/comments/{mine}').status_code == 403
    assert world.anonymous_session().delete(f'/api/comments/{mine}').status_code == 401
    assert other.delete(f'/api/comments/{ObjectId()}').status_code == 404
    assert other.delete('/api/comments/zzz').status_code == 400
    assert len(world.comments.comments) == 1


def test_author_can_delete_a_comment_after_the_video_stops_being_public(world):
    author, fan, other = world.user('Autor'), world.user('Fa'), world.user('Outro')
    video = world.video(author)
    mine = comment_on(fan, video).json['data']['id']
    world.videos.videos[video['_id']]['status'] = 'rejected'
    assert other.delete(f'/api/comments/{mine}').status_code == 404  # cannot even see the video
    assert world.visitor().get(f'/api/comments/{mine}').status_code == 404
    assert fan.delete(f'/api/comments/{mine}').status_code == 204


# --- reports -----------------------------------------------------------------------------------

def test_report_creation_and_duplicates(world):
    author, reporter, other = world.user('Autor'), world.user('Denunciante'), world.user('Outro')
    video = world.video(author)
    url = f"/api/videos/{video['_id']}/reports"
    created = reporter.post(url, json={'reason': '  Conteúdo ofensivo ao público  '})
    assert created.status_code == 201
    assert created.json['data'] == {'id': created.json['data']['id'], 'status': 'open'}
    assert 'Location' not in created.headers
    stored = world.reports.reports[ObjectId(created.json['data']['id'])]
    assert stored['reason'] == 'Conteúdo ofensivo ao público' and stored['reporter_id'] == reporter.id
    duplicate = reporter.post(url, json={'reason': 'Outra denúncia do mesmo usuário'})
    assert duplicate.status_code == 409 and duplicate.json['error']['code'] == 'already_reported'
    assert other.post(url, json={'reason': 'Conteúdo ofensivo ao público'}).status_code == 201
    assert 'reason' not in video and len(world.reports.reports) == 2
    assert world.videos.videos[video['_id']]['status'] == 'approved'  # reporting never removes content


@pytest.mark.parametrize('body', [None, {}, {'reason': 'curto'}, {'reason': 'x' * 1001}, {'reason': 7},
                                  {'reason': 'x' * 20, 'status': 'resolved'}, {'motivo': 'x' * 20}])
def test_invalid_report_is_400(world, body):
    reporter, video = world.user('Denunciante'), world.video(world.user('Autor'))
    assert reporter.post(f"/api/videos/{video['_id']}/reports", json=body).status_code == 400
    assert not world.reports.reports


def test_report_permissions(world):
    author, reporter = world.user('Autor'), world.user('Denunciante')
    approved = world.video(author)
    pending = world.video(author, status='pending')
    body = {'reason': 'Conteúdo ofensivo ao público'}
    assert world.visitor().post(f"/api/videos/{approved['_id']}/reports", json=body).status_code == 403
    assert world.anonymous_session().post(f"/api/videos/{approved['_id']}/reports", json=body).status_code == 401
    assert reporter.post(f"/api/videos/{pending['_id']}/reports", json=body).status_code == 404
    assert not world.reports.reports


# --- blocks ------------------------------------------------------------------------------------

def test_block_lifecycle(world):
    me, joao, ana = world.user('Eu'), world.user('Joao'), world.user('Ana')
    assert me.put(f'/api/me/blocks/{joao.id}').json['data'] == {'blocked': True}
    assert me.put(f'/api/me/blocks/{joao.id}').status_code == 200
    world.advance(seconds=1)
    me.put(f'/api/me/blocks/{ana.id}')
    assert len(world.blocks.blocks) == 2
    listing = me.get('/api/me/blocks')
    assert [item['user']['name'] for item in listing.json['data']] == ['Ana', 'Joao']
    assert set(listing.json['data'][0]) == {'user', 'created_at'}
    assert set(listing.json['data'][0]['user']) == {'id', 'name', 'bio', 'created_at'}
    assert 'email' not in listing.text
    assert me.delete(f'/api/me/blocks/{joao.id}').status_code == 204
    assert me.delete(f'/api/me/blocks/{joao.id}').status_code == 204
    assert [item['user']['name'] for item in me.get('/api/me/blocks').json['data']] == ['Ana']
    assert joao.get('/api/me/blocks').json['data'] == []  # blocking is one-directional and private


def test_block_validation_and_permissions(world):
    me, joao = world.user('Eu'), world.user('Joao')
    assert me.put(f'/api/me/blocks/{me.id}').status_code == 400
    assert me.put(f'/api/me/blocks/{ObjectId()}').status_code == 404
    assert me.put('/api/me/blocks/zzz').status_code == 400
    assert me.delete('/api/me/blocks/zzz').status_code == 400
    assert world.visitor().put(f'/api/me/blocks/{joao.id}').status_code == 403
    assert world.anonymous_session().put(f'/api/me/blocks/{joao.id}').status_code == 401
    assert world.visitor().get('/api/me/blocks').status_code == 401
    assert not world.blocks.blocks


def test_block_list_paginates(world):
    me = world.user('Eu')
    others = [world.user(f'U{n}') for n in range(3)]
    for other in others:
        world.advance(seconds=1)
        me.put(f'/api/me/blocks/{other.id}')
    first = me.get('/api/me/blocks?limit=2')
    second = me.get('/api/me/blocks?limit=2&cursor=' + first.json['pagination']['next_cursor'])
    names = [i['user']['name'] for i in first.json['data'] + second.json['data']]
    assert names == ['U2', 'U1', 'U0'] and second.json['pagination']['has_more'] is False


def test_social_routes_report_database_outage_as_503(world):
    author, fan = world.user('Autor'), world.user('Fa')
    video = world.video(author)
    world.comments.down = True
    assert comment_on(fan, video).status_code == 503
    world.blocks.down = True
    assert fan.get('/api/me/blocks').status_code == 503
