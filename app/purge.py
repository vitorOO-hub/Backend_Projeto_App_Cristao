"""Physical removal of deleted videos; idempotent, so it can be repeated after any failure."""
from app.access import ext
from app.auth import now


def purge_video(video):
    """Remove the file and the reactions/comments of a soft-deleted video (reports are kept)."""
    ext('storage').delete(video['file']['storage_key'])
    ext('reaction_repository').delete_for_video(video['_id'])
    ext('comment_repository').delete_for_video(video['_id'])
    ext('video_repository').mark_purged(video['_id'], now())


def purge_pending(limit=50):
    """Finish every purge that an earlier failure left incomplete; returns how many were done."""
    videos = ext('video_repository').list_pending_purge(limit)
    for video in videos:
        purge_video(video)
    return len(videos)


def recheck_deleted(limit=50, after=None):
    """Opt-in maintenance only: repeat cleanup of a bounded page of deleted videos."""
    videos, more = ext('video_repository').list_deleted_for_recheck(limit=limit, after=after)
    for video in videos:
        purge_video(video)
    return len(videos), str(videos[-1]['_id']) if more and videos else None
