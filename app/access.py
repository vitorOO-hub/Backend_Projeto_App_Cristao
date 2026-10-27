"""Loading, authorizing and serializing videos for a request; shared by every video-related route."""
import re

from bson import ObjectId
from flask import current_app

from app.auth import repository as accounts
from app.errors import ApiError
from app.videos import can_view, video_public, video_restricted

ID_PATTERN = re.compile(r'[0-9a-f]{24}')


def ext(name):
    return current_app.extensions[name]


def parse_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ApiError(400, 'validation_error', 'Identificador inválido.')
    return ObjectId(value)


def not_found():
    return ApiError(404, 'not_found', 'Recurso não encontrado.')


def is_admin(user):
    return user is not None and user['role'] == 'admin'


def blocked_by(viewer):
    """Authors the signed-in viewer hides from discovery; visitors hide nobody."""
    return ext('block_repository').blocked_ids(viewer['_id']) if viewer is not None else []


def load_video(video_id, viewer):
    """The video if this viewer may see it; otherwise 404, so a known id reveals nothing."""
    video = ext('video_repository').find_by_id(video_id)
    if video is None or not can_view(video, viewer):
        raise not_found()
    if (viewer is not None and video['author_id'] != viewer['_id'] and not is_admin(viewer)
            and ext('block_repository').exists(viewer['_id'], video['author_id'])):
        raise not_found()
    return video


def approved_video(video_id, viewer):
    """Interactions (reactions, comments, reports) exist only on approved, visible videos."""
    video = load_video(video_id, viewer)
    if video['status'] != 'approved':
        raise not_found()
    return video


def serialize_videos(videos, viewer, restricted):
    """Video JSON for a page: authors, counts and reactions are fetched once for the whole page.

    `restricted`: True for VideoOwn/Moderation, False for Video, 'auto' to expose the
    review reason only to the author and administrators.
    """
    if not videos:
        return []
    ids = [video['_id'] for video in videos]
    authors = accounts().find_users_by_ids({video['author_id'] for video in videos})
    reaction_totals = ext('reaction_repository').count_for(ids)
    comment_totals = ext('comment_repository').count_for(ids, exclude_authors=blocked_by(viewer))
    mine = ext('reaction_repository').reacted_by(viewer['_id'], ids) if viewer is not None else set()
    result = []
    for video in videos:
        author = authors.get(video['author_id'])
        if author is None:
            continue
        extras = {'reaction_count': reaction_totals.get(video['_id'], 0),
                  'comment_count': comment_totals.get(video['_id'], 0),
                  'reacted_by_me': video['_id'] in mine}
        show_review = restricted is True or (restricted == 'auto' and viewer is not None and (
            is_admin(viewer) or viewer['_id'] == video['author_id']))
        result.append((video_restricted if show_review else video_public)(video, author, **extras))
    return result
