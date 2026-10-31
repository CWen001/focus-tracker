from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from activity_log.collector import collect, observe
from activity_log.windows import Foreground
from activity_log.storage import status, StorageError
from test_protocol import message


class FakeWindows:
    @contextmanager
    def notifications(self): yield lambda: []
    def foreground(self): return Foreground('editor.exe','known')
    def input_idle_ms(self): return 0
    def session_state(self): return 'active'


class FakeBridge:
    error=None
    def snapshot(self): return message()


class FakeClock:
    def __init__(self): self.now=0
    def monotonic(self): return self.now
    def wall(self): return 1000+self.now
    def offset(self): return 0
    def sleep(self,n):
        self.now+=n
        if self.now>6: raise KeyboardInterrupt


class CollectorTests(unittest.TestCase):
    def test_background_chrome_metadata_is_not_used(self):
        observation=observe(FakeWindows(),FakeBridge(),FakeClock(),0,'active')
        self.assertIsNone(observation.domain)
        self.assertEqual(observation.playback,'unknown')

    def test_chrome_without_bridge_is_unknown_not_previous_page(self):
        windows=FakeWindows(); windows.foreground=lambda: Foreground('chrome.exe','known')
        bridge=FakeBridge()
        self.assertEqual(observe(windows,bridge,FakeClock(),0,'active').domain,'www.youtube.com')
        bridge.snapshot=lambda: None
        observed=observe(windows,bridge,FakeClock(),0,'active')
        self.assertIsNone(observed.domain)
        self.assertEqual(observed.playback,'unknown')

    def test_ctrl_c_flushes_observed_data_without_extending_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'activity.sqlite3'
            self.assertEqual(collect(path,FakeWindows(),FakeBridge(),FakeClock(),lambda text: None),130)
            result=status(path)
            self.assertTrue(result['checkpoint']['stopped'])
            self.assertGreater(result['durations']['activity'],4)
            self.assertLess(result['checkpoint']['at'],6)

    def test_unknown_initial_session_does_not_observe_foreground(self):
        with tempfile.TemporaryDirectory() as directory:
            windows=FakeWindows();windows.session_state=lambda:'unknown'
            def forbidden(): raise AssertionError('unknown session must not be treated as unlocked')
            windows.foreground=forbidden
            path=Path(directory)/'activity.sqlite3'
            collect(path,windows,FakeBridge(),FakeClock(),lambda text:None)
            self.assertGreater(status(path)['durations']['unknown'],4)

    def test_bridge_failure_stops_with_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=FakeBridge(); bridge.error='PIPE_SERVER_FAILED'
            from activity_log.pipe import PipeError
            with self.assertRaisesRegex(PipeError,'PIPE_SERVER_FAILED'):
                collect(Path(directory)/'activity.sqlite3',FakeWindows(),bridge,FakeClock(),lambda text: None)
