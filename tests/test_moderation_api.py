import pytest
from bson import ObjectId

REASON = 'Conteúdo adequado às diretrizes.'


def stamp(video):
    return video['updated_at'].isoformat().replace('+00:00', 'Z')


def decision(video, status='approved', reason=REASON, **changes):
    return {'status': status, 'reason': reason, 'expected_updated_at': stamp(video), **changes}


def captions(response):
    return [item['caption'] for item in response.json['data']]


def test_everything_in_moderation_is_admin_only(world):
    author, user, visitor = world.user('Autor'), world.user('Comum'), world.visitor()
    video = world.video(author, status='pending')
    report_id = world.reports.insert({'_id': ObjectId(), 'video_id': video['_id'], 'reporter_id': user.id,
                                      'reason': 'Conteúdo ofensivo', 'status': 'open', 'resolution_note': None,
                                      'resolved_by': None, 'created_at': world.time[0], 'resolved_at': None})['_id']
    reads = ['/api/moderation/videos', '/api/moderation/reports']
    writes = [(f"/api/moderation/videos/{video['_id']}", decision(video)),
              (f'/api/moderation/reports/{report_id}', {'status': 'resolved', 'resolution_note': 'Revisado e mantido'})]
    for path in reads:
        assert visitor.get(path).status_code == 401
        assert user.get(path).status_code == 403
        assert author.get(path).status_code == 403
    for path, body in writes:
        assert visitor.patch(path, json=body).status_code == 403  # CSRF fails first
        assert world.anonymous_session().patch(path, json=body).status_code == 401
        assert user.patch(path, json=body).status_code == 403
    assert world.videos.videos[video['_id']]['status'] == 'pending'
    assert world.reports.reports[report_id]['status'] == 'open'


def test_review_queue_lists_pending_oldest_first(world):
    author, admin = world.user('Autor'), world.admin()
    world.video(author, status='pending', caption='antigo')
    world.video(author, status='approved', caption='aprovado')
    world.video(author, status='pending', caption='novo')
    gone = world.video(author, status='pending', caption='excluído')
    world.videos.soft_delete(gone['_id'], author.id, world.time[0])
    response = admin.get('/api/moderation/videos')
    assert response.status_code == 200 and captions(response) == ['antigo', 'novo']
    assert all('review_reason' in item for item in response.json['data'])
    assert captions(admin.get('/api/moderation/videos?status=approved')) == ['aprovado']
    assert admin.get('/api/moderation/videos?status=deleted').status_code == 400
    assert not any(secret in response.text for secret in ('storage_key', 'video_codec', 'decided_by'))


def test_review_queue_paginates(world):
    author, admin = world.user('Autor'), world.admin()
    for n in range(3):
        world.video(author, status='pending', caption=f'p{n}')
    first = admin.get('/api/moderation/videos?limit=2')
    second = admin.get('/api/moderation/videos?limit=2&cursor=' + first.json['pagination']['next_cursor'])
    assert captions(first) + captions(second) == ['p0', 'p1', 'p2']
    assert admin.get('/api/moderation/videos?limit=2&status=approved&cursor=' +
                     first.json['pagination']['next_cursor']).status_code == 400


def test_approval_publishes_the_video_and_records_who_decided(world):
    author, admin, visitor = world.user('Autor'), world.admin(), world.visitor()
    video = world.video(author, status='pending')
    world.advance(minutes=1)
    response = admin.patch(f"/api/moderation/videos/{video['_id']}", json=decision(video))
    assert response.status_code == 200
    data = response.json['data']
    assert data['status'] == 'approved' and data['review_reason'] == REASON
    stored = world.videos.videos[video['_id']]
    assert stored['review']['decided_by'] == admin.id and stored['review']['decided_at'] == stored['updated_at']
    assert [item['id'] for item in visitor.get('/api/videos').json['data']] == [str(video['_id'])]
    assert 'decided_by' not in response.text


def test_rejection_hides_the_video_and_shows_the_reason_to_the_author(world):
    author, admin, visitor = world.user('Autor'), world.admin(), world.visitor()
    video = world.video(author, status='pending')
    reason = 'Contém ataques pessoais a terceiros.'
    assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=decision(video, 'rejected', reason)).status_code == 200
    assert visitor.get('/api/videos').json['data'] == []
    assert visitor.get(f"/api/videos/{video['_id']}").status_code == 404
    seen = author.get(f"/api/videos/{video['_id']}").json['data']
    assert seen['status'] == 'rejected' and seen['review_reason'] == reason


def test_decision_on_a_video_edited_after_review_started_is_409(world):
    author, admin = world.user('Autor'), world.admin()
    video = world.video(author, status='pending')
    opened = decision(video)
    world.advance(minutes=1)
    author.patch(f"/api/videos/{video['_id']}", json={'caption': 'Texto que o revisor não viu'})
    response = admin.patch(f"/api/moderation/videos/{video['_id']}", json=opened)
    assert response.status_code == 409 and response.json['error']['code'] == 'conflict'
    assert world.videos.videos[video['_id']]['status'] == 'pending'
    fresh = admin.get('/api/moderation/videos').json['data'][0]
    assert admin.patch(f"/api/moderation/videos/{video['_id']}",
                       json={**opened, 'expected_updated_at': fresh['updated_at']}).status_code == 200


def test_allowed_and_forbidden_transitions(world):
    author, admin = world.user('Autor'), world.admin()
    approved = world.video(author, status='approved')
    rejected = world.video(author, status='rejected')
    assert admin.patch(f"/api/moderation/videos/{approved['_id']}", json=decision(approved)).status_code == 409
    assert admin.patch(f"/api/moderation/videos/{rejected['_id']}", json=decision(rejected, 'rejected')).status_code == 409
    assert admin.patch(f"/api/moderation/videos/{approved['_id']}", json=decision(approved, 'rejected')).status_code == 200
    assert admin.patch(f"/api/moderation/videos/{rejected['_id']}", json=decision(rejected, 'approved')).status_code == 200


def test_decision_on_missing_or_deleted_video_is_404(world):
    author, admin = world.user('Autor'), world.admin()
    video = world.video(author, status='pending')
    body = decision(video)
    world.videos.soft_delete(video['_id'], author.id, world.time[0])
    assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=body).status_code == 404
    assert admin.patch(f'/api/moderation/videos/{ObjectId()}', json=body).status_code == 404
    assert admin.patch('/api/moderation/videos/zzz', json=body).status_code == 400


@pytest.mark.parametrize('changes', [
    {'status': 'pending'}, {'status': 'deleted'}, {'status': {'$ne': 1}}, {'reason': 'curta'},
    {'reason': 'x' * 1001}, {'reason': None}, {'expected_updated_at': 'ontem'},
    {'expected_updated_at': '2026-10-27T12:00:00'}, {'expected_updated_at': 5}, {'expected_updated_at': 'x' * 50},
    {'author_id': 'x'}, {'extra': 1}])
def test_invalid_decisions_change_nothing(world, changes):
    author, admin = world.user('Autor'), world.admin()
    video = world.video(author, status='pending')
    assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=decision(video, **changes)).status_code == 400
    assert world.videos.videos[video['_id']]['status'] == 'pending'


def test_missing_decision_fields_are_400(world):
    author, admin = world.user('Autor'), world.admin()
    video = world.video(author, status='pending')
    for field in ('status', 'reason', 'expected_updated_at'):
        body = decision(video)
        body.pop(field)
        assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=body).status_code == 400
    assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=[]).status_code == 400


def test_an_accepted_instant_in_another_offset_matches(world):
    author, admin = world.user('Autor'), world.admin()
    video = world.video(author, status='pending')
    local = video['updated_at'].astimezone(__import__('datetime').timezone(__import__('datetime').timedelta(hours=-3)))
    body = decision(video, expected_updated_at=local.isoformat())
    assert admin.patch(f"/api/moderation/videos/{video['_id']}", json=body).status_code == 200


def test_role_is_read_from_the_server_on_every_request(world):
    admin = world.admin()
    assert admin.get('/api/moderation/videos').status_code == 200
    world.auth.users[admin.user['email_normalized']]['role'] = 'user'
    assert admin.get('/api/moderation/videos').status_code == 403


# --- reports -----------------------------------------------------------------------------------

def report_for(world, video, reporter):
    world.advance(seconds=1)
    return reporter.post(f"/api/videos/{video['_id']}/reports",
                         json={'reason': 'Conteúdo ofensivo ao público'}).json['data']['id']


def test_report_queue_and_resolution_do_not_touch_the_video(world):
    author, reporter, other, admin = world.user('Autor'), world.user('Denunciante'), world.user('Outro'), world.admin()
    video = world.video(author)
    first, second = report_for(world, video, reporter), report_for(world, video, other)
    queue = admin.get('/api/moderation/reports')
    assert [item['id'] for item in queue.json['data']] == [first, second]
    item = queue.json['data'][0]
    assert set(item) == {'id', 'video_id', 'reporter_id', 'reason', 'status', 'resolution_note',
                         'created_at', 'resolved_at'}
    assert item['status'] == 'open' and item['reporter_id'] == str(reporter.id)
    world.advance(minutes=1)
    resolved = admin.patch(f'/api/moderation/reports/{first}',
                           json={'status': 'resolved', 'resolution_note': '  Revisado e mantido  '})
    assert resolved.status_code == 200
    assert resolved.json['data']['status'] == 'resolved' and resolved.json['data']['resolution_note'] == 'Revisado e mantido'
    assert resolved.json['data']['resolved_at'] is not None and 'resolved_by' not in resolved.text
    assert world.reports.reports[ObjectId(first)]['resolved_by'] == admin.id
    assert world.videos.videos[video['_id']]['status'] == 'approved'
    assert [i['id'] for i in admin.get('/api/moderation/reports').json['data']] == [second]
    assert [i['id'] for i in admin.get('/api/moderation/reports?status=resolved').json['data']] == [first]
    assert admin.get('/api/moderation/reports?status=pending').status_code == 400


def test_resolving_twice_or_a_missing_report(world):
    author, reporter, admin = world.user('Autor'), world.user('Denunciante'), world.admin()
    report_id = report_for(world, world.video(author), reporter)
    body = {'status': 'resolved', 'resolution_note': 'Revisado e mantido'}
    assert admin.patch(f'/api/moderation/reports/{report_id}', json=body).status_code == 200
    assert admin.patch(f'/api/moderation/reports/{report_id}', json=body).status_code == 409
    assert admin.patch(f'/api/moderation/reports/{ObjectId()}', json=body).status_code == 404
    assert admin.patch('/api/moderation/reports/zzz', json=body).status_code == 400


@pytest.mark.parametrize('body', [
    None, {}, {'status': 'resolved'}, {'resolution_note': 'Revisado e mantido'}, {'status': 'open', 'resolution_note': 'Revisado e mantido'},
    {'status': 'resolved', 'resolution_note': 'curta'}, {'status': 'resolved', 'resolution_note': 'x' * 1001},
    {'status': 'resolved', 'resolution_note': 'Revisado e mantido', 'resolved_by': 'x'}])
def test_invalid_resolution_is_400(world, body):
    author, reporter, admin = world.user('Autor'), world.user('Denunciante'), world.admin()
    report_id = report_for(world, world.video(author), reporter)
    assert admin.patch(f'/api/moderation/reports/{report_id}', json=body).status_code == 400
    assert world.reports.reports[ObjectId(report_id)]['status'] == 'open'
