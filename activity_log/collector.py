"""Foreground-only collection; dependencies keep tests off the user's desktop."""
from datetime import datetime
import time
from uuid import uuid4
from .pipe import PipeError
from .storage import Store
from .timeline import Observation, Timeline


class Clock:
    monotonic = staticmethod(time.monotonic)
    wall = staticmethod(time.time)
    sleep = staticmethod(time.sleep)

    @staticmethod
    def offset():
        return int(datetime.now().astimezone().utcoffset().total_seconds()/60)


def observe(windows, bridge, clock, start, system):
    app = windows.foreground().app if system == 'active' else None
    idle = windows.input_idle_ms() if system == 'active' else None
    media = bridge.snapshot() if app == 'chrome.exe' else None
    if media is not None and not media['focused']:
        media = None
    # Desktop players and uninstrumented Chrome pages are unknown, never assumed paused.
    return Observation(clock.monotonic()-start, clock.wall(), clock.offset(), app, idle,
                       media['domain'] if media else None, media['video_id'] if media else None,
                       media['title'] if media else None, media['playback'] if media else 'unknown', system)


def collect(path, windows, bridge, clock=None, output=print):
    clock = clock or Clock()
    timeline = Timeline(uuid4().hex)
    start = clock.monotonic()
    next_sample = start
    last_commit = start
    suspended = False
    with Store(path) as store, windows.notifications() as poll:
        system = windows.session_state()
        locked = system == 'locked'
        def save(stopped=False):
            previous = timeline.previous
            if previous is None:
                return
            checkpoint = {'session': timeline.session, 'utc': previous.utc, 'at': previous.at,
                          'connection': 'fresh' if bridge.snapshot() else 'unknown', 'stopped': stopped}
            store.commit(uuid4().hex, timeline.changes(), checkpoint)
            timeline.committed()

        try:
            output('RUNNING Collection active; Ctrl+C stops. Browser capability may be unknown.')
            while True:
                events = poll()
                for event in events:
                    if event == 'lock': locked = True
                    elif event == 'unlock': locked = False
                    elif event == 'suspend': suspended = True
                    elif event == 'resume':
                        suspended = False
                        system = windows.session_state()
                        locked = system == 'locked'
                        continue
                    elif event == 'session-change':
                        system = 'unknown'
                        continue
                    system = 'suspended' if suspended else 'locked' if locked else 'active'
                now = clock.monotonic()
                if bridge.error:
                    raise PipeError(bridge.error)
                if now >= next_sample or events:
                    if system == 'unknown' and not suspended:
                        system = windows.session_state()
                        locked = system == 'locked'
                    timeline.observe(observe(windows, bridge, clock, start, system))
                    next_sample = now+1
                if now-last_commit >= 5 or events:
                    save()
                    last_commit = now
                    output('CHECKPOINT Saved observed intervals; pending time is not finalized.')
                clock.sleep(.1)
        except KeyboardInterrupt:
            # Persist only observations already made, not the unobserved exit tail.
            save(stopped=True)
            output('STOPPED Saved last observation; tail after checkpoint is unobserved.')
            return 130
