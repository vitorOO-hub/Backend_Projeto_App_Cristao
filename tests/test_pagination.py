import base64
import json
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.errors import ApiError
from app.pagination import decode_cursor, encode_cursor

AT = datetime(2026, 10, 27, 10, 0, 0, 123000, tzinfo=timezone.utc)
ITEM = {'_id': ObjectId(), 'created_at': AT}


def raw(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')


def test_round_trip_preserves_position_exactly():
    token = encode_cursor(ITEM, 'feed|None|None')
    assert decode_cursor(token, 'feed|None|None') == (AT, ITEM['_id'])
    assert '=' not in token and '/' not in token and '+' not in token


def test_cursor_does_not_work_for_another_listing():
    token = encode_cursor(ITEM, 'feed|humor|None')
    with pytest.raises(ApiError) as error:
        decode_cursor(token, 'feed|louvor|None')
    assert error.value.status == 400


@pytest.mark.parametrize('token', [
    '', 'x', '!!!!', 'A' * 301, raw([]), raw('text'), raw(None), raw({}), raw({'s': 's'}),
    raw({'s': 's', 't': 'ontem', 'i': str(ITEM['_id'])}),
    raw({'s': 's', 't': '2026-10-27T10:00:00', 'i': str(ITEM['_id'])}),
    raw({'s': 's', 't': AT.isoformat(), 'i': 'not-an-object-id'}),
    raw({'s': 's', 't': AT.isoformat(), 'i': 5}), raw({'s': 's', 't': 5, 'i': str(ITEM['_id'])}),
    raw({'s': 's', 't': {'$gt': 1}, 'i': {'$ne': 1}})])
def test_forged_or_malformed_cursors_are_400(token):
    with pytest.raises(ApiError) as error:
        decode_cursor(token, 's')
    assert error.value.status == 400 and error.value.code == 'validation_error'
