"""In-memory stand-ins for the Mongo repositories, honouring the same contracts."""
from copy import deepcopy

from app.errors import DataUnavailable, ReportConflict
from app.videos import EMPTY_REVIEW, to_millis

DECISION_FROM = {'approved': ('pending', 'rejected'), 'rejected': ('pending', 'approved')}


def page(items, newest_first, limit, after):
    key = lambda item: (item['created_at'], item['_id'])
    items = sorted(items, key=key, reverse=newest_first)
    if after is not None:
        items = [item for item in items if (key(item) < after if newest_first else key(item) > after)]
    return [deepcopy(item) for item in items[:limit]], len(items) > limit


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

    def list_approved(self, *, category=None, author_id=None, exclude_authors=(), limit=10, after=None):
        self.check()
        items = [v for v in self.live() if v['status'] == 'approved'
                 and (category is None or v['category'] == category)
                 and (author_id is None or v['author_id'] == author_id)
                 and v['author_id'] not in exclude_authors]
        return page(items, True, limit, after)

    def list_by_author(self, author_id, *, status=None, limit=10, after=None):
        self.check()
        items = [v for v in self.live() if v['author_id'] == author_id and status in (None, v['status'])]
        return page(items, True, limit, after)

    def list_for_review(self, *, status='pending', limit=10, after=None):
        self.check()
        return page([v for v in self.live() if v['status'] == status], False, limit, after)

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

    def decide(self, video_id, *, expected_updated_at, status, reason, admin_id, at):
        self.check()
        video = self.videos.get(video_id)
        if (not video or video['deleted_at'] is not None or video['updated_at'] != expected_updated_at
                or video['status'] not in DECISION_FROM[status]):
            return None
        video.update(status=status, updated_at=to_millis(at),
                     review={'reason': reason, 'decided_by': admin_id, 'decided_at': to_millis(at)})
        return deepcopy(video)

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

    def list_for_video(self, video_id, *, exclude_authors=(), limit=10, after=None):
        self.check()
        items = [c for c in self.comments.values()
                 if c['video_id'] == video_id and c['author_id'] not in exclude_authors]
        return page(items, False, limit, after)

    def count_for(self, video_ids, exclude_authors=()):
        self.check()
        totals = {}
        for comment in self.comments.values():
            if comment['video_id'] in video_ids and comment['author_id'] not in exclude_authors:
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

    def blocked_ids(self, user_id, limit=1000):
        self.check()
        return [blocked for user, blocked in self.blocks if user == user_id]

    def exists(self, user_id, blocked_user_id):
        self.check()
        return (user_id, blocked_user_id) in self.blocks
