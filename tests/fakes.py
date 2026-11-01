"""In-memory stand-ins for the Mongo repositories, honouring the same contracts."""
from copy import deepcopy
from functools import wraps
from threading import RLock

from bson import ObjectId

from app.core.errors import DataUnavailable, ReportConflict, StorageUnavailable
from app.domain.videos import EMPTY_REVIEW, to_millis
from app.services.interactions import unavailable_video
from app.services.uploads import ACTIVE, MongoUploadRepository, check_admission
from app.api.auth import now

DECISION_FROM = {'approved': ('pending', 'rejected'), 'rejected': ('pending', 'approved')}


def lifecycle_write(operation):
    @wraps(operation)
    def guarded(self, *args, **kwargs):
        with self.lifecycle_lock:
            return operation(self, *args, **kwargs)
    return guarded


def page(items, newest_first, limit, after):
    key = lambda item: (item['created_at'], item['_id'])
    items = sorted(items, key=key, reverse=newest_first)
    if after is not None:
        items = [item for item in items if (key(item) < after if newest_first else key(item) > after)]
    return [deepcopy(item) for item in items[:limit]], len(items) > limit


def hidden_from(repository, author_id, viewer_id):
    return viewer_id is not None and repository.blocks.exists(viewer_id, author_id)


class Switch:
    """Lets a test simulate a database outage for any repository."""

    def __init__(self):
        self.down = False

    def check(self):
        if self.down:
            raise DataUnavailable()


class FakeVideoRepository(Switch):
    def __init__(self):
        super().__init__()
        self.videos = {}
        self.lifecycle_lock = RLock()

    def insert(self, video):
        self.check()
        self.videos[video['_id']] = deepcopy(video)
        return video

    def live(self):
        return [video for video in self.videos.values() if video['deleted_at'] is None]

    def find_by_id(self, video_id):
        self.check()
        video = self.videos.get(video_id)
        return deepcopy(video) if video and video['deleted_at'] is None else None

    def list_approved(self, *, category=None, author_id=None, exclude_authors=(), viewer_id=None,
                      limit=10, after=None):
        self.check()
        items = [v for v in self.live() if v['status'] == 'approved'
                 and (category is None or v['category'] == category)
                 and (author_id is None or v['author_id'] == author_id)
                 and v['author_id'] not in exclude_authors
                 and not hidden_from(self, v['author_id'], viewer_id)]
        return page(items, True, limit, after)

    def list_by_author(self, author_id, *, status=None, limit=10, after=None):
        self.check()
        items = [v for v in self.live() if v['author_id'] == author_id and status in (None, v['status'])]
        return page(items, True, limit, after)

    def find_approved_by_ids(self, video_ids, exclude_authors=(), *, viewer_id=None):
        self.check()
        return [deepcopy(v) for v in self.live() if v['_id'] in video_ids and v['status'] == 'approved'
                and v['author_id'] not in exclude_authors
                and not hidden_from(self, v['author_id'], viewer_id)]

    def count_approved_by_author(self, author_id):
        self.check()
        return sum(1 for v in self.live() if v['status'] == 'approved' and v['author_id'] == author_id)

    def list_for_review(self, *, status='pending', limit=10, after=None):
        self.check()
        return page([v for v in self.live() if v['status'] == status], False, limit, after)

    @lifecycle_write
    def update_metadata(self, video_id, author_id, fields, at):
        self.check()
        video = self.videos.get(video_id)
        if not video or video['deleted_at'] is not None or video['author_id'] != author_id:
            return None
        if video['status'] == 'approved':
            video['status'], video['review'] = 'pending', dict(EMPTY_REVIEW)
        video.update(fields)
        video['updated_at'] = to_millis(at)
        return deepcopy(video)

    @lifecycle_write
    def decide(self, video_id, *, expected_updated_at, status, reason, admin_id, at):
        self.check()
        video = self.videos.get(video_id)
        if (not video or video['deleted_at'] is not None or video['updated_at'] != expected_updated_at
                or video['status'] not in DECISION_FROM[status]):
            return None
        video.update(status=status, updated_at=to_millis(at),
                     review={'reason': reason, 'decided_by': admin_id, 'decided_at': to_millis(at)})
        return deepcopy(video)

    @lifecycle_write
    def soft_delete(self, video_id, author_id, at):
        self.check()
        video = self.videos.get(video_id)
        if not video or video['deleted_at'] is not None or video['author_id'] != author_id:
            return False
        video['deleted_at'] = video['updated_at'] = to_millis(at)
        return True

    def list_pending_purge(self, limit=50):
        self.check()
        return [deepcopy(v) for v in self.videos.values()
                if v['deleted_at'] is not None and v['purged_at'] is None][:limit]

    def mark_purged(self, video_id, at):
        self.check()
        video = self.videos.get(video_id)
        if not video or video['deleted_at'] is None or video['purged_at'] is not None:
            return False
        video['purged_at'] = to_millis(at)
        return True

    def list_deleted_for_recheck(self, *, limit=50, after=None):
        self.check()
        rows = sorted((v for v in self.videos.values() if v['deleted_at'] is not None
                       and (after is None or v['_id'] > after)), key=lambda v: v['_id'])
        return deepcopy(rows[:limit]), len(rows) > limit


class FakeInteractions:
    """Fixture-only atomic writes sharing the lifecycle lock with edits and deletion."""
    def __init__(self, videos, reactions, comments, reports):
        self.videos, self.reactions, self.comments, self.reports = videos, reactions, comments, reports

    def _write(self, video_id, operation):
        with self.videos.lifecycle_lock:
            self.videos.check()
            video = self.videos.videos.get(video_id)
            if video is None or video['deleted_at'] is not None or video['status'] != 'approved':
                raise unavailable_video()
            previous = (deepcopy(video), deepcopy(self.reactions.pairs),
                        deepcopy(self.comments.comments), deepcopy(self.reports.reports))
            try:
                video['interaction_version'] = video.get('interaction_version', 0) + 1
                return operation()
            except BaseException:
                self.videos.videos[video_id], self.reactions.pairs, self.comments.comments, self.reports.reports = previous
                raise

    def react(self, user_id, video_id, at):
        return self._write(video_id, lambda: self.reactions.add(user_id, video_id, at))

    def unreact(self, user_id, video_id):
        return self._write(video_id, lambda: self.reactions.remove(user_id, video_id))

    def comment(self, document):
        return self._write(document['video_id'], lambda: self.comments.insert(document))

    def report(self, document):
        return self._write(document['video_id'], lambda: self.reports.insert(document))


class FakeOrphanRepository(Switch):
    def __init__(self, videos, reactions, comments):
        super().__init__()
        self.videos, self.reactions, self.comments = videos, reactions, comments

    def list_for(self, collection, *, limit=50, after=None):
        self.check()
        self.videos.check()
        if collection == 'comments':
            self.comments.check()
            rows = self.comments.comments.values()
        else:
            self.reactions.check()
            rows = [{'_id': video_id, 'video_id': video_id} for _, video_id in self.reactions.pairs]
        rows = [row for row in rows if (after is None or row['_id'] > after)
                and (row['video_id'] not in self.videos.videos
                     or self.videos.videos[row['video_id']]['deleted_at'] is not None)]
        rows = sorted(rows, key=lambda row: row['_id'])
        return [{'_id': row['_id'], 'video_id': row['video_id']} for row in rows[:limit]], len(rows) > limit


class FakeUploadRepository(Switch):
    """Fixture-only quota ledger, serialized across fixture workers and video writes."""
    def __init__(self, videos):
        super().__init__()
        self.videos, self.reservations = videos, {}
        self.complete_error_before = self.complete_error_after = self.reserve_error_after = False

    def reserve(self, metadata, policy, user_bytes, volume_bytes, capacity):
        with self.videos.lifecycle_lock:
            self.check()
            self.videos.check()
            videos = [v for v in self.videos.videos.values() if v['author_id'] == metadata['author_id']]
            own = [r for r in self.reservations.values()
                   if r['author_id'] == metadata['author_id'] and r['state'] in ACTIVE]
            volume = [r for r in self.reservations.values()
                      if r['volume_id'] == metadata['volume_id'] and r['state'] in ACTIVE]
            used = sum(v['file']['bytes'] for v in videos
                       if v.get('purged_at') is None and v.get('file_removed_at') is None)
            pending = sum(v['status'] == 'pending' and v['deleted_at'] is None for v in videos)
            check_admission(policy, used, pending, sum(r['user_bytes'] for r in own),
                            sum(r['pending_slots'] for r in own), sum(r['volume_bytes'] for r in volume),
                            user_bytes, volume_bytes, capacity())
            if metadata['_id'] in self.reservations:
                raise DataUnavailable()
            self.reservations[metadata['_id']] = {**metadata, 'state': 'reserved', 'user_bytes': user_bytes,
                                                  'volume_bytes': volume_bytes, 'pending_slots': 1}
            if self.reserve_error_after:
                raise DataUnavailable()

    def complete(self, metadata, video):
        with self.videos.lifecycle_lock:
            self.check()
            if self.complete_error_before:
                raise DataUnavailable()
            record = self.reservations.get(metadata['_id'])
            if (record is None or record['state'] != 'reserved'
                    or not MongoUploadRepository._matches(record, metadata)
                    or video['file']['bytes'] > record['user_bytes']):
                raise StorageUnavailable()
            self.videos.insert(video)
            record.update(state='committed', video_id=video['_id'])
            if self.complete_error_after:
                raise DataUnavailable()

    def cancel(self, metadata):
        with self.videos.lifecycle_lock:
            self.check()
            record = self.reservations.get(metadata['_id'])
            if not MongoUploadRepository._matches(record, metadata) or record and record['state'] == 'committed':
                raise StorageUnavailable()
            self.reservations.setdefault(metadata['_id'], {**metadata, 'user_bytes': 0, 'volume_bytes': 0,
                                                            'pending_slots': 0})['state'] = 'cancelled'

    def inspect(self, metadata):
        self.check()
        record = self.reservations.get(metadata['_id'])
        if not MongoUploadRepository._matches(record, metadata):
            raise StorageUnavailable()
        found = any(v['file']['storage_key'] == metadata['storage_key'] for v in self.videos.videos.values())
        return 'committed' if found else (record['state'] if record else 'unreserved')

    def prepare_recovery(self, metadata):
        with self.videos.lifecycle_lock:
            self.check()
            state = self.inspect(metadata)
            record = self.reservations.get(metadata['_id'])
            if state == 'committed' and any(v['file']['storage_key'] == metadata['storage_key']
                                            for v in self.videos.videos.values()):
                if record and record['state'] != 'cancelled':
                    record['state'] = 'committed'
                return 'keep'
            if state == 'committed':
                return 'manual'
            if record and record['state'] == 'cancelled':
                return 'discard'
            self.reservations.setdefault(metadata['_id'], {**metadata, 'user_bytes': 0, 'volume_bytes': 0,
                                                            'pending_slots': 0})['state'] = 'recovering'
            return 'discard'

    def mark_file_removed(self, video, volume_id):
        with self.videos.lifecycle_lock:
            self.check()
            stored = self.videos.videos.get(video['_id'])
            if stored and stored['deleted_at'] is not None and stored.get('file_removed_at') is None:
                stored['file_removed_at'] = to_millis(now())


class FakeReactionRepository(Switch):
    def __init__(self):
        super().__init__()
        self.pairs = {}

    def add(self, user_id, video_id, at):
        self.check()
        self.pairs.setdefault((user_id, video_id), to_millis(at))

    def remove(self, user_id, video_id):
        self.check()
        self.pairs.pop((user_id, video_id), None)

    def count_for(self, video_ids):
        self.check()
        totals = {}
        for _, video_id in self.pairs:
            if video_id in video_ids:
                totals[video_id] = totals.get(video_id, 0) + 1
        return totals

    def reacted_by(self, user_id, video_ids):
        self.check()
        return {video_id for user, video_id in self.pairs if user == user_id and video_id in video_ids}

    def visible_video(self, video_id, viewer_id):
        if viewer_id is None:
            return True
        video = self.videos.videos.get(video_id)
        return (video is not None and video['deleted_at'] is None and video['status'] == 'approved'
                and not hidden_from(self, video['author_id'], viewer_id))

    def list_by_user(self, user_id, *, viewer_id=None, limit=10, after=None):
        self.check()
        items = [{'_id': video_id, 'user_id': user, 'video_id': video_id, 'created_at': at}
                 for (user, video_id), at in self.pairs.items()
                 if user == user_id and self.visible_video(video_id, viewer_id)]
        return page(items, True, limit, after)

    def count_by_user(self, user_id, *, viewer_id=None):
        self.check()
        return sum(1 for user, video_id in self.pairs
                   if user == user_id and self.visible_video(video_id, viewer_id))

    def count_received(self, author_id):
        """Reactions on the author's approved, undeleted videos; `videos` is wired by the test world."""
        self.check()
        mine = {v['_id'] for v in self.videos.live() if v['status'] == 'approved' and v['author_id'] == author_id}
        return sum(1 for _, video_id in self.pairs if video_id in mine)

    def delete_for_video(self, video_id):
        self.check()
        gone = [pair for pair in self.pairs if pair[1] == video_id]
        for pair in gone:
            del self.pairs[pair]
        return len(gone)


class FakeCommentRepository(Switch):
    def __init__(self):
        super().__init__()
        self.comments = {}

    def insert(self, comment):
        self.check()
        self.comments[comment['_id']] = deepcopy(comment)
        return comment

    def find_by_id(self, comment_id):
        self.check()
        return deepcopy(self.comments.get(comment_id))

    def list_for_video(self, video_id, *, exclude_authors=(), viewer_id=None, limit=10, after=None):
        self.check()
        items = [c for c in self.comments.values()
                 if c['video_id'] == video_id and c['author_id'] not in exclude_authors
                 and not hidden_from(self, c['author_id'], viewer_id)]
        return page(items, False, limit, after)

    def count_for(self, video_ids, exclude_authors=(), *, viewer_id=None):
        self.check()
        totals = {}
        for comment in self.comments.values():
            if (comment['video_id'] in video_ids and comment['author_id'] not in exclude_authors
                    and not hidden_from(self, comment['author_id'], viewer_id)):
                totals[comment['video_id']] = totals.get(comment['video_id'], 0) + 1
        return totals

    def delete(self, comment_id, author_id):
        self.check()
        comment = self.comments.get(comment_id)
        if not comment or comment['author_id'] != author_id:
            return False
        del self.comments[comment_id]
        return True

    def delete_for_video(self, video_id):
        self.check()
        gone = [key for key, c in self.comments.items() if c['video_id'] == video_id]
        for key in gone:
            del self.comments[key]
        return len(gone)


class FakeReportRepository(Switch):
    def __init__(self):
        super().__init__()
        self.reports = {}

    def insert(self, report):
        self.check()
        if any(r['status'] == 'open' and r['video_id'] == report['video_id']
               and r['reporter_id'] == report['reporter_id'] for r in self.reports.values()):
            raise ReportConflict()
        self.reports[report['_id']] = deepcopy(report)
        return report

    def find_by_id(self, report_id):
        self.check()
        return deepcopy(self.reports.get(report_id))

    def list_by_status(self, *, status='open', limit=10, after=None):
        self.check()
        return page([r for r in self.reports.values() if r['status'] == status], False, limit, after)

    def resolve(self, report_id, *, note, admin_id, at):
        self.check()
        report = self.reports.get(report_id)
        if not report or report['status'] != 'open':
            return None
        report.update(status='resolved', resolution_note=note, resolved_by=admin_id,
                      resolved_at=to_millis(at))
        return deepcopy(report)


class FakeBlockRepository(Switch):
    def __init__(self):
        super().__init__()
        self.blocks = {}

    def add(self, user_id, blocked_user_id, at):
        self.check()
        self.blocks.setdefault((user_id, blocked_user_id), to_millis(at))

    def remove(self, user_id, blocked_user_id):
        self.check()
        self.blocks.pop((user_id, blocked_user_id), None)

    def list_for(self, user_id, *, limit=10, after=None):
        self.check()
        items = [{'_id': blocked, 'user_id': user, 'blocked_user_id': blocked, 'created_at': at}
                 for (user, blocked), at in self.blocks.items() if user == user_id]
        return page(items, True, limit, after)

    def exists(self, user_id, blocked_user_id):
        self.check()
        return (user_id, blocked_user_id) in self.blocks


class FakeFollowRepository(Switch):
    def __init__(self):
        super().__init__()
        self.follows = {}

    def add(self, follower_id, followee_id, at):
        self.check()
        self.follows.setdefault((follower_id, followee_id), (ObjectId(), to_millis(at)))

    def remove(self, follower_id, followee_id):
        self.check()
        self.follows.pop((follower_id, followee_id), None)

    def rows(self, user_id, side, excluded, viewer_id=None):
        other = 1 - side
        return [{'_id': key_id, 'follower_id': pair[0], 'followee_id': pair[1], 'created_at': at}
                for pair, (key_id, at) in self.follows.items()
                if pair[side] == user_id and pair[other] not in excluded
                and not hidden_from(self, pair[other], viewer_id)]

    def list_followers(self, user_id, *, exclude_users=(), viewer_id=None, limit=10, after=None):
        self.check()
        return page(self.rows(user_id, 1, exclude_users, viewer_id), True, limit, after)

    def list_following(self, user_id, *, exclude_users=(), viewer_id=None, limit=10, after=None):
        self.check()
        return page(self.rows(user_id, 0, exclude_users, viewer_id), True, limit, after)

    def count_followers(self, user_id, exclude_users=(), *, viewer_id=None):
        self.check()
        return len(self.rows(user_id, 1, exclude_users, viewer_id))

    def count_following(self, user_id, exclude_users=(), *, viewer_id=None):
        self.check()
        return len(self.rows(user_id, 0, exclude_users, viewer_id))
