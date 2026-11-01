"""Comments, reports and blocks: validation, construction and public JSON."""
from bson import ObjectId

from app.api.auth import check_strings, iso, user_public
from app.core.constants import COMMENT_MAX, REPORT_REASON_RANGE
from app.core.errors import ApiError
from app.domain.videos import invalid, to_millis


def clean_text(payload, field, low, high):
    """The body must be exactly `{field: text}`; the text is trimmed and length-checked."""
    if not isinstance(payload, dict) or set(payload) != {field}:
        raise invalid()
    check_strings((payload[field],))
    text = payload[field].strip()
    if not low <= len(text) <= high:
        raise invalid()
    return text


def require_ids(*values):
    if not all(isinstance(value, ObjectId) for value in values):
        raise ValueError('Identificadores devem vir da sessão e da URL já validados.')


def new_comment(video_id, author_id, payload, at):
    require_ids(video_id, author_id)
    return {'_id': ObjectId(), 'video_id': video_id, 'author_id': author_id,
            'text': clean_text(payload, 'text', 1, COMMENT_MAX), 'created_at': to_millis(at)}


def new_report(video_id, reporter_id, payload, at):
    require_ids(video_id, reporter_id)
    return {'_id': ObjectId(), 'video_id': video_id, 'reporter_id': reporter_id,
            'reason': clean_text(payload, 'reason', *REPORT_REASON_RANGE), 'status': 'open',
            'resolution_note': None, 'resolved_by': None, 'created_at': to_millis(at),
            'resolved_at': None}


def validate_resolution(payload):
    """PATCH /moderation/reports/{id}: `status` is always `resolved`; returns the note."""
    if not isinstance(payload, dict) or set(payload) != {'status', 'resolution_note'} \
            or payload['status'] != 'resolved':
        raise invalid()
    return clean_text({'resolution_note': payload['resolution_note']}, 'resolution_note',
                      *REPORT_REASON_RANGE)


def check_block(user_id, blocked_user_id):
    require_ids(user_id, blocked_user_id)
    if user_id == blocked_user_id:
        raise ApiError(400, 'validation_error', 'Não é possível bloquear a si mesmo.')


def comment_public(comment, author):
    return {'id': str(comment['_id']), 'video_id': str(comment['video_id']),
            'author': user_public(author), 'text': comment['text'],
            'created_at': iso(comment['created_at'])}


def report_public(report):
    """Administrators only; `resolved_by` stays in the database."""
    resolved_at = report['resolved_at']
    return {'id': str(report['_id']), 'video_id': str(report['video_id']),
            'reporter_id': str(report['reporter_id']), 'reason': report['reason'],
            'status': report['status'], 'resolution_note': report['resolution_note'],
            'created_at': iso(report['created_at']),
            'resolved_at': iso(resolved_at) if resolved_at else None}


def block_public(block, user):
    return {'user': user_public(user), 'created_at': iso(block['created_at'])}
