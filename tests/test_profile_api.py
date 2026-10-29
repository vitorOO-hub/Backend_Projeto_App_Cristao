"""Own profile: counters, reacted videos, followers/following and the follow action."""
from bson import ObjectId

OTHER_ID = '0' * 24


def follow(actor, target):
    return actor.put(f'/api/users/{target.id}/follow')


def react(actor, video):
    return actor.put(f"/api/videos/{video['_id']}/reaction")


def ids(response):
    return [item['id'] for item in response.json['data']]


OWN_ROUTES = ['/api/me/profile', '/api/me/reacted-videos', '/api/me/followers', '/api/me/following']


# --- access ------------------------------------------------------------------------------------

def test_every_profile_route_needs_login(world):
    for path in OWN_ROUTES:
        assert world.visitor().get(path).status_code == 401
    assert world.visitor().put(f'/api/users/{OTHER_ID}/follow').status_code == 403  # no CSRF either


def test_the_user_always_comes_from_the_session_never_from_the_request(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    world.video(bia)
    for path in OWN_ROUTES:
        for param in ('user_id', 'id', 'author_id'):
            assert ana.get(f'{path}?{param}={bia.id}').status_code == 400
    profile = ana.get('/api/me/profile').json['data']
    assert profile['user']['id'] == str(ana.id)
    assert profile['counts']['videos'] == 0


# --- counters ----------------------------------------------------------------------------------

def test_counters_count_only_what_is_visible_and_only_mine(world):
    ana, bia, caio = world.user('Ana'), world.user('Bia'), world.user('Caio')
    first, second = world.video(ana), world.video(ana)
    world.video(ana, status='pending')
    world.video(ana, status='rejected')
    deleted = world.video(ana)
    ana.delete(f"/api/videos/{deleted['_id']}")
    bias = world.video(bia)
    react(bia, first), react(caio, first), react(caio, second), react(ana, bias)
    follow(ana, bia), follow(ana, caio), follow(bia, ana)

    counts = ana.get('/api/me/profile').json['data']['counts']
    assert counts == {'videos': 2, 'amens_received': 3, 'reacted': 1, 'followers': 1, 'following': 2}
    assert bia.get('/api/me/profile').json['data']['counts'] == {
        'videos': 1, 'amens_received': 1, 'reacted': 1, 'followers': 1, 'following': 1}


def test_profile_shows_the_own_account_data_and_nothing_secret(world):
    ana = world.user('Ana')
    data = ana.get('/api/me/profile').json['data']['user']
    assert data['id'] == str(ana.id) and data['email'] == ana.user['email_normalized']
    assert 'password_hash' not in str(data) and 'sessions' not in str(data)


def test_a_new_account_has_all_counters_at_zero(world):
    counts = world.user().get('/api/me/profile').json['data']['counts']
    assert counts == {'videos': 0, 'amens_received': 0, 'reacted': 0, 'followers': 0, 'following': 0}


# --- reacted videos ----------------------------------------------------------------------------

def test_reacted_videos_are_newest_reaction_first_and_public_shaped(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    old, new = world.video(bia), world.video(bia)
    react(ana, old)
    world.advance(minutes=1)
    react(ana, new)

    response = ana.get('/api/me/reacted-videos')
    assert ids(response) == [str(new['_id']), str(old['_id'])]
    first = response.json['data'][0]
    assert first['reacted_by_me'] is True and first['author']['id'] == str(bia.id)
    assert 'review_reason' not in first and 'email' not in str(first)


def test_reacted_videos_hide_what_stopped_being_visible_or_whose_author_is_blocked(world):
    ana, bia, caio = world.user('Ana'), world.user('Bia'), world.user('Caio')
    kept, rejected, blocked = world.video(bia), world.video(bia), world.video(caio)
    for video in (kept, rejected, blocked):
        react(ana, video)
    world.videos.videos[rejected['_id']]['status'] = 'rejected'
    ana.put(f'/api/me/blocks/{caio.id}')

    assert ids(ana.get('/api/me/reacted-videos')) == [str(kept['_id'])]


def test_reacted_videos_of_one_user_never_show_in_anothers(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    video = world.video(bia)
    react(ana, video)
    assert ids(bia.get('/api/me/reacted-videos')) == []


def test_reacted_videos_paginate_without_repeating(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    videos = []
    for _ in range(5):
        videos.append(world.video(bia))
        react(ana, videos[-1])
        world.advance(minutes=1)
    first = ana.get('/api/me/reacted-videos?limit=2')
    assert first.json['pagination']['has_more'] is True
    second = ana.get(f"/api/me/reacted-videos?limit=2&cursor={first.json['pagination']['next_cursor']}")
    third = ana.get(f"/api/me/reacted-videos?limit=2&cursor={second.json['pagination']['next_cursor']}")
    seen = ids(first) + ids(second) + ids(third)
    assert seen == [str(v['_id']) for v in reversed(videos)]
    assert third.json['pagination'] == {'next_cursor': None, 'has_more': False}


# --- follow / unfollow -------------------------------------------------------------------------

def test_follow_is_idempotent_and_can_be_undone(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    assert follow(ana, bia).json['data'] == {'following': True}
    assert follow(ana, bia).status_code == 200
    assert ana.get('/api/me/profile').json['data']['counts']['following'] == 1
    assert bia.get('/api/me/profile').json['data']['counts']['followers'] == 1
    assert ana.delete(f'/api/users/{bia.id}/follow').status_code == 204
    assert ana.delete(f'/api/users/{bia.id}/follow').status_code == 204
    assert ana.get('/api/me/profile').json['data']['counts']['following'] == 0


def test_follow_rejects_yourself_unknown_users_and_bad_ids(world):
    ana = world.user('Ana')
    assert follow(ana, ana).status_code == 400
    assert ana.put(f'/api/users/{ObjectId()}/follow').status_code == 404
    assert ana.put('/api/users/not-an-id/follow').status_code == 400
    assert ana.put('/api/users/%7B%22%24ne%22%3Anull%7D/follow').status_code == 400
    assert world.follows.follows == {}


def test_follow_needs_a_csrf_token(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    assert ana.put(f'/api/users/{bia.id}/follow', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert ana.delete(f'/api/users/{bia.id}/follow', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert world.follows.follows == {}


# --- followers / following lists ---------------------------------------------------------------

def test_follower_and_following_lists_show_only_public_user_data(world):
    ana, bia, caio = world.user('Ana'), world.user('Bia'), world.user('Caio')
    follow(bia, ana), follow(ana, caio)

    followers = ana.get('/api/me/followers')
    following = ana.get('/api/me/following')
    assert ids(followers) == [str(bia.id)] and ids(following) == [str(caio.id)]
    for item in followers.json['data'] + following.json['data']:
        assert set(item) == {'id', 'name', 'username', 'bio', 'created_at'}


def test_lists_are_newest_first_and_paginate(world):
    ana = world.user('Ana')
    fans = []
    for n in range(5):
        fans.append(world.user(f'Fa{n}'))
        follow(fans[-1], ana)
        world.advance(minutes=1)
    first = ana.get('/api/me/followers?limit=3')
    second = ana.get(f"/api/me/followers?limit=3&cursor={first.json['pagination']['next_cursor']}")
    assert ids(first) + ids(second) == [str(f.id) for f in reversed(fans)]
    assert second.json['pagination']['has_more'] is False


def test_blocked_users_leave_my_lists_and_my_counters(world):
    ana, bia, caio = world.user('Ana'), world.user('Bia'), world.user('Caio')
    follow(bia, ana), follow(caio, ana), follow(ana, bia)
    ana.put(f'/api/me/blocks/{bia.id}')

    assert ids(ana.get('/api/me/followers')) == [str(caio.id)]
    assert ids(ana.get('/api/me/following')) == []
    counts = ana.get('/api/me/profile').json['data']['counts']
    assert counts['followers'] == 1 and counts['following'] == 0


def test_page_limits_are_enforced_on_every_list(world):
    ana = world.user('Ana')
    for path in OWN_ROUTES[1:]:
        assert ana.get(f'{path}?limit=31').status_code == 400
        assert ana.get(f'{path}?limit=0').status_code == 400
        assert ana.get(f'{path}?limit=abc').status_code == 400
        assert ana.get(f'{path}?cursor=lixo').status_code == 400
        assert ana.get(f'{path}?limit=30').status_code == 200


def test_a_cursor_from_one_list_does_not_work_on_another(world):
    ana = world.user('Ana')
    for _ in range(3):
        follow(world.user(), ana)
        world.advance(minutes=1)
    cursor = ana.get('/api/me/followers?limit=1').json['pagination']['next_cursor']
    assert ana.get(f'/api/me/following?cursor={cursor}').status_code == 400


def test_database_outage_is_503_without_details(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    world.follows.down = True
    for response in (follow(ana, bia), ana.get('/api/me/followers'), ana.get('/api/me/profile')):
        assert response.status_code == 503
        assert response.json['error']['code'] == 'data_unavailable'


def test_following_is_rate_limited(world):
    ana, bia = world.user('Ana'), world.user('Bia')
    codes = [follow(ana, bia).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
