"""Synthetic observations; native mutex tests never read foreground/input."""
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from activity_log.windows import Windows, Foreground, InputClock, notification_event

ROOT = Path(__file__).resolve().parents[1]


class InputClockTests(unittest.TestCase):
    def test_idle_duration_and_fresh_input(self):
        clock = InputClock()
        self.assertEqual(clock.sample(10000, 9000), 1000)
        self.assertEqual(clock.sample(11000, 10900), 100)

    def test_last_input_can_cross_32_bit_wrap(self):
        clock = InputClock()
        self.assertEqual(clock.sample(2**32 - 5, 2**32 - 10), 5)
        self.assertEqual(clock.sample(2**32 + 20, 7), 13)

    def test_backward_input_is_unknown_until_valid_input_returns(self):
        clock = InputClock()
        self.assertEqual(clock.sample(10000, 9000), 1000)
        self.assertIsNone(clock.sample(11000, 8000))
        self.assertIsNone(clock.sample(12000, 8000))
        self.assertEqual(clock.sample(13000, 12500), 500)

    def test_bad_or_ambiguous_ticks_are_unknown(self):
        for now, tick in ((1, 2), (100, -1), (100, 2**32), (True, 0),
                          (-1, 0), (2**31 + 10, 0), (100, True)):
            with self.subTest(now=now, tick=tick):
                self.assertIsNone(InputClock().sample(now, tick))

    def test_uptime_regression_is_unknown(self):
        clock = InputClock()
        clock.sample(10000, 9000)
        self.assertIsNone(clock.sample(8000, 7000))

    def test_unchanged_input_after_full_wrap_does_not_look_recent(self):
        clock = InputClock()
        self.assertEqual(clock.sample(10000, 9000), 1000)
        self.assertIsNone(clock.sample(2**32 + 10000, 9000))
        self.assertIsNone(clock.sample(2**32 + 11000, 9000))
        self.assertEqual(clock.sample(2**32 + 12000, 11500), 500)


class SessionStateTests(unittest.TestCase):
    def test_query_failure_does_not_assume_unlocked(self):
        api=Windows.__new__(Windows)
        api.wtsapi32=SimpleNamespace(WTSQuerySessionInformationW=Mock(return_value=False),WTSFreeMemory=Mock())
        self.assertEqual(api.session_state(),'unknown')

    def test_lock_unlock_unknown_and_disconnected_are_distinct(self):
        from activity_log.windows import WTSINFOEX
        for flag,connected,expected in [(0,0,'locked'),(1,0,'active'),(-1,0,'unknown'),(1,4,'unknown')]:
            data=WTSINFOEX();data.Level=1;data.Data.SessionFlags=flag;data.Data.SessionState=connected
            def query(server,session,kind,buffer,size):
                buffer._obj.value=ctypes.addressof(data);size._obj.value=ctypes.sizeof(data);return True
            api=Windows.__new__(Windows)
            api.wtsapi32=SimpleNamespace(WTSQuerySessionInformationW=query,WTSFreeMemory=Mock())
            self.assertEqual(api.session_state(),expected)
            api.wtsapi32.WTSFreeMemory.assert_called_once()


class ForegroundTests(unittest.TestCase):
    def setUp(self):
        self.api = Windows.__new__(Windows)
        self.api.user32 = SimpleNamespace(
            GetForegroundWindow=Mock(return_value=100),
            GetWindowThreadProcessId=Mock(side_effect=self.pid),
        )
        self.api.kernel32 = SimpleNamespace(
            OpenProcess=Mock(return_value=200),
            QueryFullProcessImageNameW=Mock(side_effect=self.image),
            CloseHandle=Mock(return_value=True),
        )

    @staticmethod
    def pid(hwnd, output):
        output._obj.value = 42
        return 1

    @staticmethod
    def image(handle, flags, buffer, size):
        buffer.value = r'C:\private-folder\Chrome.EXE'
        return True

    def test_only_executable_basename_leaves_boundary(self):
        result = self.api.foreground()
        self.assertEqual(result, Foreground('chrome.exe', 'known'))
        self.assertNotIn('private-folder', repr(result))

    def test_null_window_has_no_application(self):
        self.api.user32.GetForegroundWindow.return_value = 0
        self.assertEqual(self.api.foreground(), Foreground(None, 'no-window'))

    def test_disappeared_process_is_unknown(self):
        self.api.user32.GetWindowThreadProcessId.return_value = 0
        self.api.user32.GetWindowThreadProcessId.side_effect = None
        self.assertEqual(self.api.foreground(), Foreground(None, 'process-unavailable'))

    def test_access_denied_is_unknown(self):
        self.api.kernel32.OpenProcess.return_value = 0
        self.assertEqual(self.api.foreground(), Foreground(None, 'process-unavailable'))

    def test_query_failure_closes_handle_and_does_not_reuse_name(self):
        self.api.foreground()
        self.api.kernel32.QueryFullProcessImageNameW.side_effect = None
        self.api.kernel32.QueryFullProcessImageNameW.return_value = False
        self.assertEqual(self.api.foreground(), Foreground(None, 'process-unavailable'))
        self.assertEqual(self.api.kernel32.CloseHandle.call_count, 2)

    def test_window_switch_during_sample_discards_observation(self):
        self.api.user32.GetForegroundWindow.side_effect = [100, 101]
        self.assertEqual(self.api.foreground(), Foreground(None, 'changed-during-sample'))


class NotificationTests(unittest.TestCase):
    def fake_api(self, registered=True):
        api = Windows.__new__(Windows)
        api.kernel32 = SimpleNamespace(GetModuleHandleW=Mock(return_value=1))
        api.user32 = SimpleNamespace(
            RegisterClassW=Mock(return_value=1), UnregisterClassW=Mock(return_value=True),
            CreateWindowExW=Mock(return_value=123), DestroyWindow=Mock(return_value=True),
            DefWindowProcW=Mock(return_value=0), PeekMessageW=Mock(return_value=False),
        )
        api.wtsapi32 = SimpleNamespace(
            WTSRegisterSessionNotification=Mock(return_value=registered),
            WTSUnRegisterSessionNotification=Mock(return_value=True),
        )
        return api

    def test_notification_resources_close_after_body_failure(self):
        api = self.fake_api()
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with api.notifications() as poll:
                self.assertEqual(poll(), [])
                raise RuntimeError('synthetic')
        api.wtsapi32.WTSUnRegisterSessionNotification.assert_called_once_with(123)
        api.user32.DestroyWindow.assert_called_once_with(123)
        api.user32.UnregisterClassW.assert_called_once()

    def test_registration_failure_destroys_window_without_false_success(self):
        from activity_log.windows import ProbeError
        api = self.fake_api(registered=False)
        with self.assertRaisesRegex(ProbeError, 'SESSION_NOTIFICATION_FAILED'):
            with api.notifications():
                self.fail('must not start without registration')
        api.wtsapi32.WTSUnRegisterSessionNotification.assert_not_called()
        api.user32.DestroyWindow.assert_called_once_with(123)
        api.user32.UnregisterClassW.assert_called_once()

    def test_event_names_keep_resume_distinct_from_input(self):
        self.assertEqual(notification_event(0x2B1, 7), 'lock')
        self.assertEqual(notification_event(0x2B1, 8), 'unlock')
        self.assertEqual(notification_event(0x2B1, 4), 'session-change')
        self.assertEqual(notification_event(0x218, 4), 'suspend')
        self.assertEqual(notification_event(0x218, 18), 'resume')
        self.assertEqual(notification_event(0x218, 7), 'resume')
        self.assertIsNone(notification_event(0x218, 10))
        self.assertIsNone(notification_event(123, 7))


class ProbeCLITests(unittest.TestCase):
    def call_probe(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'tools.probe_windows', *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=10)

    def test_refusal_never_constructs_an_observer(self):
        from tools.probe_windows import main
        with patch('tools.probe_windows.Windows', side_effect=AssertionError('must not observe')):
            with patch('sys.stderr'):
                self.assertEqual(main([]), 2)
                with self.assertRaises(SystemExit) as exit_result:
                    main(['--allow-observation', '--seconds', '0'])
                self.assertEqual(exit_result.exception.code, 2)

    def test_probe_without_opt_in_refuses(self):
        result = self.call_probe('--seconds', '1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('OBSERVATION_NOT_AUTHORIZED', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_invalid_duration_rejects_before_observation(self):
        result = self.call_probe('--allow-observation', '--seconds', '0')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_help_does_not_observe(self):
        result = self.call_probe('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--allow-observation', result.stdout)


@unittest.skipUnless(sys.platform == 'win32', 'Windows kernel mutex')
class NativeMutexTests(unittest.TestCase):
    CHILD = '''
import sys
from pathlib import Path
from activity_log.windows import Windows, AlreadyRunning
try:
    with Windows().single_instance(sys.argv[1]):
        if len(sys.argv) == 3:
            Path(sys.argv[2]).write_text('ready', encoding='utf-8')
            sys.stdin.read()
except AlreadyRunning:
    sys.exit(4)
'''

    def test_second_process_refused_and_release_allows_restart(self):
        name = 'Local\\ActivityLog.Test.' + uuid4().hex
        command = [sys.executable, '-B', '-c', self.CHILD, name]
        with Windows().single_instance(name):
            blocked = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
            self.assertEqual(blocked.returncode, 4, blocked.stderr)
        reopened = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)

    def test_process_termination_releases_the_instance(self):
        name = 'Local\\ActivityLog.Test.' + uuid4().hex
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'ready'
            child = subprocess.Popen([sys.executable, '-B', '-c', self.CHILD, name, str(ready)],
                                     cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), 'child failed to acquire synthetic mutex')
                child.terminate()
                child.wait(timeout=5)
                with Windows().single_instance(name):
                    pass
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
