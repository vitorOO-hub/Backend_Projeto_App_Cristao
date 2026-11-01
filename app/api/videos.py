"""Feed, publication, own videos and media delivery (RF02-RF05)."""
from flask import Blueprint, Response, current_app, jsonify, request, send_file

from app.api.access import (ext, load_video, not_found, parse_id, serialize_videos, viewer_id)
from app.api.auth import authenticated_user, csrf_user, json_payload, now, optional_user, read_session
from app.core.constants import CATEGORIES, VIDEO_STATUSES
from app.core.errors import ApiError
from app.api.pagination import bad_request, page_params, page_response, query_args
from app.services.purge import purge_video
from app.services.storage import FileTooLarge
from app.domain.videos import new_video, validate_metadata

videos = Blueprint('videos', __name__, url_prefix='/api')


def forbidden():
    return ApiError(403, 'forbidden', 'Permissão insuficiente.')


def require_author(video, user):
    if video['author_id'] != user['_id']:
        raise forbidden()


@videos.get('/videos')
def feed():
    viewer = optional_user()
    args = query_args({'category', 'author_id', 'cursor', 'limit'})
    category = args.get('category')
    if category is not None and category not in CATEGORIES:
        raise bad_request()
    author_id = parse_id(args['author_id']) if 'author_id' in args else None
    scope = f'feed|{category}|{author_id}'
    limit, after = page_params(args, scope)
    items, more = ext('video_repository').list_approved(
        category=category, author_id=author_id, viewer_id=viewer_id(viewer),
        limit=limit, after=after)
    return page_response(items, more, scope, serialize_videos(items, viewer, False))


@videos.get('/me/videos')
def mine():
    user = authenticated_user(read_session(now()))
    args = query_args({'status', 'cursor', 'limit'})
    status = args.get('status')
    if status is not None and status not in VIDEO_STATUSES:
        raise bad_request()
    scope = f'mine|{status}'
    limit, after = page_params(args, scope)
    items, more = ext('video_repository').list_by_author(user['_id'], status=status, limit=limit, after=after)
    return page_response(items, more, scope, serialize_videos(items, user, True))


@videos.post('/videos')
def create():
    user = csrf_user()
    if request.mimetype != 'multipart/form-data':
        raise ApiError(415, 'unsupported_media_type', 'Envie o vídeo como multipart/form-data.')
    # Neither request.form nor request.files may be accessed before admission:
    # Flask's parser would already receive and spool the file at that point.
    attempt = ext('upload_manager').begin(user['_id'])
    request.upload_attempt = attempt
    try:
        if any(len(request.form.getlist(key)) != 1 for key in request.form):
            raise bad_request('Dados inválidos.')
        metadata = validate_metadata(request.form.to_dict())
        if set(request.files) != {'file'} or len(request.files.getlist('file')) != 1:
            raise bad_request('Envie exatamente um arquivo no campo "file".')
        upload = request.files['file']
        if not (upload.filename or '').lower().endswith('.mp4'):
            raise ApiError(415, 'unsupported_media_type', 'Somente arquivos .mp4 são aceitos.')
        try:
            temp, size = ext('storage').receive(upload.stream, current_app.config['MAX_VIDEO_BYTES'],
                                                temp_dir=attempt.lease.directory)
        except FileTooLarge:
            raise ApiError(413, 'file_too_large', 'O vídeo excede o limite de 50 MiB.') from None
        request.close()  # release parser spools before probing/committing the private copy
        info = ext('media_probe')(temp)
        video = new_video(user['_id'], metadata, {'storage_key': attempt.key, 'bytes': size, **info}, now())
        attempt.publish(temp, video)
    except BaseException:
        request.close()
        attempt.abort()
        raise
    finally:
        attempt.close()
    response = jsonify(data=serialize_videos([video], user, True)[0])
    response.status_code = 201
    response.headers['Location'] = f"/api/videos/{video['_id']}"
    return response


@videos.get('/videos/<video_id>')
def detail(video_id):
    viewer = optional_user()
    items = serialize_videos([load_video(parse_id(video_id), viewer)], viewer, 'auto')
    if not items:
        raise not_found()
    return jsonify(data=items[0])


@videos.patch('/videos/<video_id>')
def edit(video_id):
    user = csrf_user()
    video = load_video(parse_id(video_id), user)
    require_author(video, user)
    fields = validate_metadata(json_payload(), partial=True)
    updated = ext('video_repository').update_metadata(video['_id'], user['_id'], fields, now())
    if updated is None:
        raise not_found()
    return jsonify(data=serialize_videos([updated], user, True)[0])


@videos.delete('/videos/<video_id>')
def remove(video_id):
    user = csrf_user()
    video = load_video(parse_id(video_id), user)
    require_author(video, user)
    if not ext('video_repository').soft_delete(video['_id'], user['_id'], now()):
        raise not_found()
    try:
        purge_video(video)
    except Exception as error:  # the video is already invisible; `flask purge-videos` finishes the job
        current_app.logger.warning('Limpeza física pendente para um vídeo excluído (%s).', type(error).__name__)
    return Response(status=204)


@videos.get('/videos/<video_id>/media')
def media(video_id):
    video = load_video(parse_id(video_id), optional_user())
    path = ext('storage').path(video['file']['storage_key'])
    if not path.is_file():
        raise not_found()
    response = send_file(path, mimetype='video/mp4', conditional=True, etag=False, max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
