"""Video documents: metadata validation, construction, visibility and public JSON."""
import re

from bson import ObjectId

from app.auth import check_strings, iso, user_public
from app.constants import BIBLE_REFERENCE_MAX, CAPTION_MAX, CATEGORIES, MAX_VIDEO_SECONDS
from app.errors import ApiError

METADATA_FIELDS = {'caption', 'category', 'bible_reference'}
FILE_FIELDS = {'storage_key', 'bytes', 'duration_seconds', 'video_codec', 'audio_codec', 'width', 'height'}
STORAGE_KEY = re.compile(r'[0-9a-f]{32}\.mp4')
EMPTY_REVIEW = {'reason': None, 'decided_by': None, 'decided_at': None}


def to_millis(value):
    """BSON dates hold milliseconds; truncate so values round-trip through the database."""
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def invalid():
    return ApiError(400, 'validation_error', 'Dados inválidos.')


def validate_metadata(payload, partial=False):
    """Client-editable fields only; partial=True is a PATCH with at least one field."""
    required = set() if partial else {'caption', 'category'}
    if (not isinstance(payload, dict) or not payload or not set(payload) <= METADATA_FIELDS
            or not required <= set(payload)):
        raise invalid()
    clean = {}
    if 'caption' in payload:
        check_strings((payload['caption'],))
        clean['caption'] = payload['caption'].strip()
        if not 1 <= len(clean['caption']) <= CAPTION_MAX:
            raise invalid()
    if 'category' in payload:
        if not isinstance(payload['category'], str) or payload['category'] not in CATEGORIES:
            raise invalid()
        clean['category'] = payload['category']
    if 'bible_reference' in payload:
        value = payload['bible_reference']
        if value is not None:
            check_strings((value,))
            value = value.strip() or None
        if value is not None and len(value) > BIBLE_REFERENCE_MAX:
            raise invalid()
        clean['bible_reference'] = value
    return clean


def validate_file_info(file):
    """Technical data produced by the server (ffprobe + storage); a violation is a server bug."""
    if (not isinstance(file, dict) or set(file) != FILE_FIELDS
            or not isinstance(file['storage_key'], str) or not STORAGE_KEY.fullmatch(file['storage_key'])
            or not isinstance(file['bytes'], int) or file['bytes'] <= 0
            or not isinstance(file['duration_seconds'], (int, float))
            or not 0 < file['duration_seconds'] <= MAX_VIDEO_SECONDS
            or not isinstance(file['video_codec'], str)):
        raise ValueError('Dados técnicos do arquivo inválidos.')
    return dict(file)


def new_video(author_id, metadata, file, at):
    """A pending video; author, status, dates and file data never come from the client."""
    if not isinstance(author_id, ObjectId):
        raise ValueError('Autor deve vir da sessão validada.')
    metadata = validate_metadata(metadata)
    at = to_millis(at)
    return {'_id': ObjectId(), 'author_id': author_id, 'caption': metadata['caption'],
            'category': metadata['category'], 'bible_reference': metadata.get('bible_reference'),
            'status': 'pending', 'file': validate_file_info(file), 'review': dict(EMPTY_REVIEW),
            'deleted_at': None, 'purged_at': None, 'created_at': at, 'updated_at': at}


def can_view(video, viewer):
    """Authorization for one video: approved is public; otherwise only its author or an admin."""
    if video.get('deleted_at') is not None:
        return False
    if video['status'] == 'approved':
        return True
    return viewer is not None and (viewer['role'] == 'admin' or viewer['_id'] == video['author_id'])


def video_public(video, author, *, reaction_count=0, comment_count=0, reacted_by_me=False):
    """The Video object of docs/api.md; `file` internals and review notes never leave here."""
    return {'id': str(video['_id']), 'author': user_public(author), 'caption': video['caption'],
            'category': video['category'], 'bible_reference': video['bible_reference'],
            'status': video['status'], 'created_at': iso(video['created_at']),
            'updated_at': iso(video['updated_at']),
            'duration_seconds': video['file']['duration_seconds'],
            'media_url': f"/api/videos/{video['_id']}/media", 'reaction_count': reaction_count,
            'comment_count': comment_count, 'reacted_by_me': reacted_by_me}


def video_restricted(video, author, **counts):
    """VideoOwn/Moderation: for the author and administrators only."""
    return {**video_public(video, author, **counts), 'review_reason': video['review']['reason']}
