from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import OperationFailure

from app import create_app
from app.repositories.indexes import INDEXES, ensure_indexes
from app.core.errors import DataUnavailable


def spec(collection):
    return {model.document['name']: model.document for model in INDEXES[collection]}


def keys(collection, name):
    return list(spec(collection)[name]['key'].items())


def test_planned_collections_and_unique_index_names():
    assert set(INDEXES) == {'videos', 'reactions', 'comments', 'reports', 'blocks', 'follows',
                            'upload_guards', 'upload_reservations'}
    names = [name for collection in INDEXES for name in spec(collection)]
    assert len(names) == len(set(names))
    assert all(name == '_id_' or name.startswith(collection + '_')
               for collection in INDEXES for name in spec(collection))


def test_video_indexes_serve_feed_author_and_purge():
    videos = spec('videos')
    assert keys('videos', 'videos_feed') == [('status', 1), ('created_at', -1), ('_id', -1)]
    assert keys('videos', 'videos_feed_category') == [('status', 1), ('category', 1), ('created_at', -1), ('_id', -1)]
    assert keys('videos', 'videos_author') == [('author_id', 1), ('created_at', -1), ('_id', -1)]
    assert videos['videos_purge']['partialFilterExpression'] == {'deleted_at': {'$type': 'date'}}
    assert not any(model.get('unique') for model in videos.values())


def test_uniqueness_enforces_one_reaction_one_block_one_open_report():
    assert spec('reactions')['reactions_user_video_unique']['unique']
    assert keys('reactions', 'reactions_user_video_unique') == [('user_id', 1), ('video_id', 1)]
    assert spec('blocks')['blocks_pair_unique']['unique']
    open_report = spec('reports')['reports_open_unique']
    assert open_report['unique'] and open_report['partialFilterExpression'] == {'status': 'open'}


def test_comments_and_report_queue_are_stable_keysets():
    assert keys('comments', 'comments_video') == [('video_id', 1), ('created_at', 1), ('_id', 1)]
    assert keys('reports', 'reports_queue') == [('status', 1), ('created_at', 1), ('_id', 1)]


def test_no_ttl_on_permanent_data():
    assert not any('expireAfterSeconds' in model for collection in INDEXES for model in spec(collection).values())


def test_ensure_indexes_creates_every_collection():
    database = {name: MagicMock() for name in INDEXES}
    ensure_indexes(database)
    for name, models in INDEXES.items():
        database[name].create_indexes.assert_called_once_with(models)


def test_ensure_indexes_failure_is_controlled():
    database = {name: MagicMock() for name in INDEXES}
    database['reactions'].create_indexes.side_effect = OperationFailure('private uri')
    with pytest.raises(DataUnavailable) as error:
        ensure_indexes(database)
    assert 'private' not in str(error.value)


def test_init_db_without_uri_has_no_local_fallback(app):
    result = app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code == 1
    assert 'MONGODB_URI' in result.output and 'Traceback' not in result.output


def test_init_db_with_uri_creates_indexes_in_configured_database():
    app = create_app({'TESTING': True, 'APP_ENV': 'testing', 'SESSION_COOKIE_SECURE': False,
                      'MONGODB_URI': 'mongodb://localhost:1', 'MONGODB_DATABASE': 'unit_test_db'})
    with patch('app.ensure_indexes') as ensure:
        result = app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code == 0
    assert ensure.call_args.args[0].name == 'unit_test_db'


def test_init_db_failure_is_a_controlled_message():
    app = create_app({'TESTING': True, 'APP_ENV': 'testing', 'SESSION_COOKIE_SECURE': False,
                      'MONGODB_URI': 'mongodb://user:secret@localhost:1'})
    with patch('app.ensure_indexes', side_effect=DataUnavailable()):
        result = app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code == 1
    assert 'secret' not in result.output and 'Traceback' not in result.output


def test_video_data_is_503_without_database(app):
    repository = app.extensions['video_repository']
    with pytest.raises(DataUnavailable):
        repository.find_by_id(None)


def test_data_unavailable_maps_to_generic_503(app):
    @app.get('/boom')
    def boom():
        raise DataUnavailable()

    response = app.test_client().get('/boom')
    assert response.status_code == 503
    assert response.json['error']['code'] == 'data_unavailable'
    assert response.headers['Cache-Control'] == 'no-store'
