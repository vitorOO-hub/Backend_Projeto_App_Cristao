"""Opaque keyset cursors and query-string handling shared by every listing."""
import base64
import json
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from flask import jsonify, request

from app.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.errors import ApiError


def bad_request(message='Parâmetros inválidos.'):
    return ApiError(400, 'validation_error', message)


def query_args(allowed):
    """Single-valued query parameters; an unknown or repeated one is a 400."""
    args = {}
    for key in request.args:
        values = request.args.getlist(key)
        if key not in allowed or len(values) != 1:
            raise bad_request()
        args[key] = values[0]
    return args


def encode_cursor(item, scope):
    raw = json.dumps({'t': item['created_at'].isoformat(), 'i': str(item['_id']), 's': scope},
                     separators=(',', ':'))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def decode_cursor(token, scope):
    """A cursor only works for the listing and filters that produced it."""
    try:
        if len(token) > 300:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
        if data['s'] != scope:
            raise ValueError
        at = datetime.fromisoformat(data['t'])
        if at.tzinfo is None:
            raise ValueError
        return at, ObjectId(data['i'])
    except (ValueError, KeyError, TypeError, AttributeError, InvalidId):
        raise bad_request('Cursor inválido.') from None


def page_params(args, scope):
    limit = DEFAULT_PAGE_SIZE
    if 'limit' in args:
        text = args['limit']
        if not (text.isascii() and text.isdigit() and len(text) <= 3) or not 1 <= int(text) <= MAX_PAGE_SIZE:
            raise bad_request()
        limit = int(text)
    after = decode_cursor(args['cursor'], scope) if 'cursor' in args else None
    return limit, after


def page_response(items, has_more, scope, data):
    """`items` are the raw documents of the page; `data` is their already serialized form."""
    cursor = encode_cursor(items[-1], scope) if has_more and items else None
    return jsonify(data=data, pagination={'next_cursor': cursor, 'has_more': has_more})
