"""The signed-in user's own profile: counters, reacted videos, followers and following (RF08)."""
from flask import Blueprint, Response, jsonify

from app.api.access import ext, not_found, parse_id, serialize_videos
from app.api.auth import authenticated_user, csrf_user, now, read_session, user_public, user_self
from app.api.auth import repository as accounts
from app.core.errors import ApiError
from app.api.pagination import page_params, page_response, query_args

profile = Blueprint('profile', __name__, url_prefix='/api')


def current_user():
    """The account always comes from the session; no route here reads a user id from the request."""
    return authenticated_user(read_session(now()))


@profile.get('/me/profile')
def my_profile():
    user = current_user()
    query_args(set())
    follows = ext('follow_repository')
    counts = {
        'videos': ext('video_repository').count_approved_by_author(user['_id']),
        'amens_received': ext('reaction_repository').count_received(user['_id']),
        'reacted': ext('reaction_repository').count_by_user(user['_id'], viewer_id=user['_id']),
        'followers': follows.count_followers(user['_id'], viewer_id=user['_id']),
        'following': follows.count_following(user['_id'], viewer_id=user['_id']),
    }
    return jsonify(data={'user': user_self(user), 'counts': counts})


@profile.get('/me/reacted-videos')
def reacted_videos():
    """Videos the user said "Amém" to. A page can come back shorter than `limit` when a video it
    points to stopped being visible; `has_more` is what says whether there is another page."""
    user = current_user()
    limit, after = page_params(query_args({'cursor', 'limit'}), 'reacted')
    reactions, more = ext('reaction_repository').list_by_user(
        user['_id'], viewer_id=user['_id'], limit=limit, after=after)
    found = ext('video_repository').find_approved_by_ids(
        [item['video_id'] for item in reactions], viewer_id=user['_id'])
    by_id = {video['_id']: video for video in found}
    visible = [by_id[item['video_id']] for item in reactions if item['video_id'] in by_id]
    return page_response(reactions, more, 'reacted', serialize_videos(visible, user, False))


def user_page(scope, field, fetch_name):
    user = current_user()
    limit, after = page_params(query_args({'cursor', 'limit'}), scope)
    fetch = getattr(ext('follow_repository'), fetch_name)
    items, more = fetch(user['_id'], viewer_id=user['_id'], limit=limit, after=after)
    users = accounts().find_users_by_ids({item[field] for item in items})
    data = [user_public(users[item[field]]) for item in items if item[field] in users]
    return page_response(items, more, scope, data)


@profile.get('/me/followers')
def followers():
    return user_page('followers', 'follower_id', 'list_followers')


@profile.get('/me/following')
def following():
    return user_page('following', 'followee_id', 'list_following')


@profile.put('/users/<user_id>/follow')
def follow(user_id):
    user = csrf_user()
    target = parse_id(user_id)
    if target == user['_id']:
        raise ApiError(400, 'validation_error', 'Você não pode seguir a si mesmo.')
    if accounts().find_user_by_id(target) is None:
        raise not_found()
    ext('follow_repository').add(user['_id'], target, now())
    return jsonify(data={'following': True})


@profile.delete('/users/<user_id>/follow')
def unfollow(user_id):
    user = csrf_user()
    ext('follow_repository').remove(user['_id'], parse_id(user_id))
    return Response(status=204)
