"""Review queue and report handling for administrators (RF11, RF12)."""
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from app.api.access import ext, not_found, parse_id, serialize_videos
from app.api.auth import (authenticated_user, csrf_user, json_payload, now, read_session,
                      require_admin)
from app.core.constants import REPORT_STATUSES, REVIEW_REASON_RANGE, VIDEO_STATUSES
from app.core.errors import ApiError
from app.api.pagination import bad_request, page_params, page_response, query_args
from app.domain.social import clean_text, report_public, validate_resolution
from app.domain.videos import to_millis

moderation = Blueprint('moderation', __name__, url_prefix='/api')


def admin_reader():
    return require_admin(authenticated_user(read_session(now())))


def changed():
    return ApiError(409, 'conflict', 'O item foi alterado. Recarregue e revise novamente.')


def parse_instant(value):
    if not isinstance(value, str) or len(value) > 40:
        raise bad_request('Dados inválidos.')
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise bad_request('Dados inválidos.') from None
    if at.tzinfo is None:
        raise bad_request('Dados inválidos.')
    return to_millis(at.astimezone(timezone.utc))


@moderation.get('/moderation/videos')
def review_queue():
    admin = admin_reader()
    args = query_args({'status', 'cursor', 'limit'})
    status = args.get('status', 'pending')
    if status not in VIDEO_STATUSES:
        raise bad_request()
    scope = f'review|{status}'
    limit, after = page_params(args, scope)
    items, more = ext('video_repository').list_for_review(status=status, limit=limit, after=after)
    return page_response(items, more, scope, serialize_videos(items, admin, True))


@moderation.patch('/moderation/videos/<video_id>')
def decide(video_id):
    admin = require_admin(csrf_user())
    video_id = parse_id(video_id)
    payload = json_payload()
    if not isinstance(payload, dict) or set(payload) != {'status', 'reason', 'expected_updated_at'}:
        raise bad_request('Dados inválidos.')
    if payload['status'] not in ('approved', 'rejected'):
        raise bad_request('Dados inválidos.')
    reason = clean_text({'reason': payload['reason']}, 'reason', *REVIEW_REASON_RANGE)
    expected = parse_instant(payload['expected_updated_at'])
    videos = ext('video_repository')
    video = videos.decide(video_id, expected_updated_at=expected, status=payload['status'],
                          reason=reason, admin_id=admin['_id'], at=now())
    if video is None:
        # Distinguish "gone" from "changed since it was reviewed or not eligible for this change".
        if videos.find_by_id(video_id) is None:
            raise not_found()
        raise changed()
    items = serialize_videos([video], admin, True)
    if not items:
        raise not_found()
    return jsonify(data=items[0])


@moderation.get('/moderation/reports')
def report_queue():
    admin_reader()
    args = query_args({'status', 'cursor', 'limit'})
    status = args.get('status', 'open')
    if status not in REPORT_STATUSES:
        raise bad_request()
    scope = f'reports|{status}'
    limit, after = page_params(args, scope)
    items, more = ext('report_repository').list_by_status(status=status, limit=limit, after=after)
    return page_response(items, more, scope, [report_public(item) for item in items])


@moderation.patch('/moderation/reports/<report_id>')
def resolve_report(report_id):
    admin = require_admin(csrf_user())
    report_id = parse_id(report_id)
    note = validate_resolution(json_payload())
    reports = ext('report_repository')
    report = reports.resolve(report_id, note=note, admin_id=admin['_id'], at=now())
    if report is None:
        if reports.find_by_id(report_id) is None:
            raise not_found()
        raise changed()
    return jsonify(data=report_public(report))
