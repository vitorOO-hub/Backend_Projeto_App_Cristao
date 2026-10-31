"""Server-side visibility filters that never materialize a viewer's whole block list."""
from bson import ObjectId


def unblocked_stages(viewer_id, author_field='author_id'):
    if viewer_id is None:
        return []
    if not isinstance(viewer_id, ObjectId):
        raise ValueError('O consultante deve ser um ObjectId validado.')
    if author_field not in ('author_id', 'follower_id', 'followee_id'):
        raise ValueError('Campo de autor inválido.')
    return [
        {'$lookup': {'from': 'blocks', 'localField': author_field,
                     'foreignField': 'blocked_user_id',
                     'pipeline': [{'$match': {'user_id': viewer_id}}, {'$limit': 1},
                                  {'$project': {'_id': 1}}],
                     'as': '_viewer_block'}},
        {'$match': {'_viewer_block': {'$size': 0}}},
        {'$unset': '_viewer_block'},
    ]


def visible_reaction_stages(viewer_id):
    """Keep reactions whose parent video is public and visible to this viewer."""
    visibility = unblocked_stages(viewer_id)
    return [
        {'$lookup': {'from': 'videos', 'localField': 'video_id', 'foreignField': '_id',
                     'pipeline': [{'$match': {'status': 'approved', 'deleted_at': None}},
                                  *visibility, {'$project': {'_id': 1}}, {'$limit': 1}],
                     'as': '_visible_video'}},
        {'$match': {'_visible_video': {'$size': 1}}},
        {'$unset': '_visible_video'},
    ]
