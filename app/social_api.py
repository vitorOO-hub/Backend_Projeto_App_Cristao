"""Reactions, comments, reports and blocks (RF06, RF07, RF09, RF10)."""
from flask import Blueprint, Response, jsonify, request

from app.access import approved_video, ext, not_found, parse_id, viewer_id
from app.auth import authenticated_user, csrf_user, json_payload, now, optional_user, read_session
from app.auth import repository as accounts
from app.errors import ApiError, ReportConflict
from app.pagination import page_params, page_response, query_args
from app.social import (block_public, check_block, comment_public, new_comment, new_report)

social = Blueprint('social', __name__, url_prefix='/api')


@social.put('/videos/<video_id>/reaction')
def react(video_id):
    user = csrf_user()
    video = approved_video(parse_id(video_id), user)
    ext('interactions_service').react(user['_id'], video['_id'], now())
    total = ext('reaction_repository').count_for([video['_id']]).get(video['_id'], 0)
    return jsonify(data={'reacted': True, 'reaction_count': total})


@social.delete('/videos/<video_id>/reaction')
def unreact(video_id):
    user = csrf_user()
    video = approved_video(parse_id(video_id), user)
    ext('interactions_service').unreact(user['_id'], video['_id'])
    return Response(status=204)


@social.get('/videos/<video_id>/comments')
def comments(video_id):
    viewer = optional_user()
    video = approved_video(parse_id(video_id), viewer)
    scope = f"comments|{video['_id']}"
    limit, after = page_params(query_args({'cursor', 'limit'}), scope)
    items, more = ext('comment_repository').list_for_video(
        video['_id'], viewer_id=viewer_id(viewer), limit=limit, after=after)
    authors = accounts().find_users_by_ids({item['author_id'] for item in items})
    data = [comment_public(item, authors[item['author_id']]) for item in items
            if item['author_id'] in authors]
    return page_response(items, more, scope, data)


@social.post('/videos/<video_id>/comments')
def create_comment(video_id):
    user = csrf_user()
    video = approved_video(parse_id(video_id), user)
    comment = new_comment(video['_id'], user['_id'], json_payload(), now())
    ext('interactions_service').comment(comment)
    response = jsonify(data=comment_public(comment, user))
    response.status_code = 201
    response.headers['Location'] = f"/api/comments/{comment['_id']}"
    return response


def find_comment(comment_id, viewer):
    comment = ext('comment_repository').find_by_id(parse_id(comment_id))
    if comment is None:
        raise not_found()
    approved_video(comment['video_id'], viewer)
    return comment


@social.get('/comments/<comment_id>')
def comment_detail(comment_id):
    viewer = optional_user()
    comment = find_comment(comment_id, viewer)
    if viewer is not None and ext('block_repository').exists(viewer['_id'], comment['author_id']):
        raise not_found()
    author = accounts().find_user_by_id(comment['author_id'])
    if author is None:
        raise not_found()
    return jsonify(data=comment_public(comment, author))


@social.delete('/comments/<comment_id>')
def delete_comment(comment_id):
    user = csrf_user()
    comment = ext('comment_repository').find_by_id(parse_id(comment_id))
    if comment is None:
        raise not_found()
    if comment['author_id'] != user['_id']:
        approved_video(comment['video_id'], user)  # hides comments on videos the caller cannot see
        raise ApiError(403, 'forbidden', 'Permissão insuficiente.')
    # The author may delete even after the video stopped being public.
    if not ext('comment_repository').delete(comment['_id'], user['_id']):
        raise not_found()
    return Response(status=204)


@social.post('/videos/<video_id>/reports')
def report(video_id):
    user = csrf_user()
    video = approved_video(parse_id(video_id), user)
    document = new_report(video['_id'], user['_id'], json_payload(), now())
    try:
        ext('interactions_service').report(document)
    except ReportConflict:
        raise ApiError(409, 'already_reported', 'Você já denunciou este vídeo.') from None
    response = jsonify(data={'id': str(document['_id']), 'status': 'open'})
    response.status_code = 201
    return response


@social.get('/me/blocks')
def list_blocks():
    user = authenticated_user(read_session(now()))
    limit, after = page_params(query_args({'cursor', 'limit'}), 'blocks')
    items, more = ext('block_repository').list_for(user['_id'], limit=limit, after=after)
    users = accounts().find_users_by_ids({item['blocked_user_id'] for item in items})
    data = [block_public(item, users[item['blocked_user_id']]) for item in items
            if item['blocked_user_id'] in users]
    return page_response(items, more, 'blocks', data)


@social.put('/me/blocks/<user_id>')
def block(user_id):
    user = csrf_user()
    target = parse_id(user_id)
    check_block(user['_id'], target)
    if accounts().find_user_by_id(target) is None:
        raise not_found()
    ext('block_repository').add(user['_id'], target, now())
    return jsonify(data={'blocked': True})


@social.delete('/me/blocks/<user_id>')
def unblock(user_id):
    user = csrf_user()
    ext('block_repository').remove(user['_id'], parse_id(user_id))
    return Response(status=204)
