"""Revision-aware intervals; no platform calls or persistence."""
from dataclasses import dataclass, replace
import math
import re


@dataclass(frozen=True)
class Observation:
    at: float
    utc: float
    offset: int
    app: str | None
    idle: int | None
    domain: str | None = None
    video_id: str | None = None
    title: str | None = None
    playback: str = 'not-playing'
    system: str = 'active'


@dataclass(frozen=True)
class Segment:
    id: str
    revision: int
    start: float
    end: float
    utc_start: float
    utc_end: float
    offset: int
    state: str
    app: str | None
    domain: str | None
    video_id: str | None
    title: str | None
    playback: str


def validate(observation):
    o = observation
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in (o.at, o.utc)) or o.at < 0:
        raise ValueError('INVALID_TIME')
    if type(o.offset) is not int or not -1440 <= o.offset <= 1440:
        raise ValueError('INVALID_OFFSET')
    if o.idle is not None and (type(o.idle) is not int or o.idle < 0):
        raise ValueError('INVALID_IDLE')
    if o.playback not in ('playing', 'not-playing', 'unknown') or o.system not in ('active', 'locked', 'suspended', 'unknown'):
        raise ValueError('INVALID_STATE')
    if o.app is not None and (not isinstance(o.app, str) or not re.fullmatch(r'[^\\/:\x00-\x1f]{1,255}', o.app)):
        raise ValueError('INVALID_APP')
    if o.domain is not None and (not isinstance(o.domain, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', o.domain)):
        raise ValueError('INVALID_DOMAIN')
    if o.app != 'chrome.exe' and any(x is not None for x in (o.domain, o.video_id, o.title)):
        raise ValueError('INVALID_PAGE')
    youtube = o.domain in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be')
    if not youtube and (o.video_id is not None or o.title is not None):
        raise ValueError('INVALID_METADATA')
    if o.video_id is not None and (not isinstance(o.video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', o.video_id)):
        raise ValueError('INVALID_VIDEO')
    if o.title is not None and (not isinstance(o.title, str) or len(o.title) > 300 or any(ord(c) < 32 or ord(c) == 127 for c in o.title)):
        raise ValueError('INVALID_TITLE')


class Timeline:
    def __init__(self, session: str):
        self.session = session
        self.segments = []
        self.previous = None
        self.candidate_start = None
        self.candidate_indices = []
        self.dirty = set()

    def _revise(self, index, **changes):
        old = self.segments[index]
        self.segments[index] = replace(old, revision=old.revision+1, **changes)
        self.dirty.add(index)

    def _settle(self, state):
        for index in self.candidate_indices:
            old = self.segments[index]
            result = 'unknown' if state == 'idle' and old.playback == 'unknown' else state
            self._revise(index, state=result)
        self.candidate_indices.clear()

    def _break_candidate(self):
        self.candidate_start = None
        self.candidate_indices.clear()

    def observe(self, current: Observation):
        validate(current)
        old = self.previous
        if old is None:
            self.previous = current
            return
        if current.at < old.at:
            raise ValueError('OUT_OF_ORDER')
        if current.at == old.at:
            if current != old:
                raise ValueError('CONFLICT')
            return
        dt = current.at-old.at
        gap = (dt > 3 and old.system not in ('locked', 'suspended')) or abs((current.utc-old.utc)-dt) > 2
        new_input = current.idle is not None and (old.idle is None or current.idle < old.idle+dt*1000-100)
        if gap:
            state = 'unknown'
            self._break_candidate()
        elif old.system in ('locked', 'suspended'):
            state = old.system
            self._break_candidate()
        elif old.system == 'unknown' or old.app is None or old.idle is None:
            state = 'unknown'
            self._break_candidate()
        elif old.playback == 'playing':
            state = 'activity'
            self._settle('activity')
            self._break_candidate()
        elif current.idle is None:
            state = 'unknown'
            self._break_candidate()
        elif new_input:
            state = 'activity'
            self._settle('activity')
            self._break_candidate()
        else:
            if self.candidate_start is None:
                self.candidate_start = old.at
            state = 'pending' if current.at-self.candidate_start < 300 else ('unknown' if old.playback == 'unknown' else 'idle')
            if current.at-self.candidate_start >= 300:
                self._settle('idle')
        identity = (old.app, old.domain, old.video_id, old.title, old.playback)
        if state in ('locked', 'suspended', 'unknown') and (gap or old.system != 'active' or old.app is None):
            identity = (None, None, None, None, 'unknown')
        segment = Segment(f'{self.session}:{len(self.segments)}', 1, old.at, current.at,
                          old.utc, old.utc+dt, old.offset, state, *identity)
        last = self.segments[-1] if self.segments else None
        # Never merge a new candidate into an interrupted, unresolved candidate.
        merge_pending = state != 'pending' or bool(self.candidate_indices)
        if last and merge_pending and last.end == segment.start and last.utc_end == segment.utc_start and (
            last.offset, last.state, last.app, last.domain, last.video_id, last.title, last.playback
        ) == (segment.offset, segment.state, *identity):
            index = len(self.segments)-1
            self._revise(index, end=segment.end, utc_end=segment.utc_end)
        else:
            index = len(self.segments)
            self.segments.append(segment)
            self.dirty.add(index)
        if state == 'pending' and index not in self.candidate_indices:
            self.candidate_indices.append(index)
        if not gap and current.system == 'active' and current.playback == 'playing':
            self._settle('activity')
            self._break_candidate()
        if current.system != 'active':
            self._break_candidate()
        self.previous = current

    def changes(self):
        return [self.segments[index] for index in sorted(self.dirty)]

    def committed(self):
        self.dirty.clear()
