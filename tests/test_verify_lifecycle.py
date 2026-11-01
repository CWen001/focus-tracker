import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from tools.verify_lifecycle import check_stopped, enable_ctrl_c, read_snapshot, require_no_browser_host


class VerificationTests(unittest.TestCase):
    def test_command_requires_consent_before_creating_artifacts(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'tools.verify_lifecycle'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('OBSERVATION_NOT_AUTHORIZED', result.stderr)

    def test_readback_omits_private_fields_and_preserves_row_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'activity.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE checkpoint(payload TEXT);'
                                 'CREATE TABLE segments(id TEXT, payload TEXT);'
                                 'CREATE TABLE sessions(id TEXT);')
                db.execute('INSERT INTO checkpoint VALUES (?)', (json.dumps({'session': 's', 'stopped': True, 'at': 5}),))
                db.execute('INSERT INTO segments VALUES (?,?)', ('s:1', json.dumps({'app': 'private.exe', 'title': 'secret', 'start': 0, 'end': 5})))
                db.execute("INSERT INTO sessions VALUES ('s')")
            db.close()
            before = path.read_bytes()
            result = read_snapshot(path)
            self.assertEqual(result['foreground_segments'], 1)
            self.assertEqual(result['seconds'], 5)
            self.assertIn('s:1', result['rows'])
            self.assertNotIn('private.exe', json.dumps(result))
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows console signal')
    def test_ctrl_c_reaches_the_owned_console(self):
        enable_ctrl_c()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'console.txt'
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            code = "import time\nprint('READY',flush=True)\ntry:\n while True: time.sleep(.1)\nexcept KeyboardInterrupt:\n print('STOPPED',flush=True)\n raise SystemExit(130)\n"
            with output.open('wb') as stream:
                process = subprocess.Popen([sys.executable, '-c', code], stdout=stream, stderr=stream,
                                           creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
                try:
                    deadline = time.monotonic() + 5
                    while 'READY' not in output.read_text():
                        self.assertIsNone(process.poll())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.05)
                    helper = subprocess.run([sys.executable, '-B', '-c',
                                             'from tools.verify_lifecycle import interrupt; import sys; interrupt(int(sys.argv[1]))',
                                             str(process.pid)], capture_output=True, timeout=5)
                    self.assertEqual(helper.returncode, 0, helper.stderr)
                    self.assertEqual(process.wait(timeout=5), 130)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
            self.assertIn('STOPPED', output.read_text())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows registry views')
    def test_host_preflight_refuses_each_registration_and_access_denied(self):
        import winreg
        combinations = [(root, view) for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
                        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)]
        for found in combinations:
            def inspect(root, key, reserved, access):
                if (root, access & (winreg.KEY_WOW64_32KEY | winreg.KEY_WOW64_64KEY)) == found:
                    return MagicMock()
                raise FileNotFoundError()
            with self.subTest(found=found), patch('winreg.OpenKey', side_effect=inspect):
                with self.assertRaisesRegex(RuntimeError, 'Registered Chrome host'):
                    require_no_browser_host()
        with patch('winreg.OpenKey', side_effect=PermissionError()):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
                require_no_browser_host()
        with patch('winreg.OpenKey', side_effect=FileNotFoundError()) as opened:
            require_no_browser_host()
            self.assertEqual(opened.call_count, 4)

    def test_stop_cannot_discard_live_foreground_records(self):
        live = {'checkpoint': {'session': 'current', 'stopped': False}, 'current_foreground_segments': 2,
                'rows': {'current:1': 'first', 'current:2': 'second'}}
        stopped = {'checkpoint': {'session': 'current', 'stopped': True},
                   'current_foreground_segments': 0, 'sessions': 1, 'rows': {}}
        with self.assertRaisesRegex(RuntimeError, 'Foreground records did not survive'):
            check_stopped(live, stopped, 0)
        stopped['current_foreground_segments'] = 2
        stopped['rows'] = live['rows'].copy()
        check_stopped(live, stopped, 0)

    def test_stop_preserves_all_live_row_ids_but_allows_payload_revisions(self):
        live = {'checkpoint': {'session': 'current', 'stopped': False}, 'current_foreground_segments': 2,
                'rows': {'current:1': 'first', 'current:2': 'second'}}
        stopped = {'checkpoint': {'session': 'current', 'stopped': True}, 'sessions': 1,
                   'current_foreground_segments': 1, 'rows': {'current:1': 'first'}}
        with self.assertRaisesRegex(RuntimeError, 'Checkpointed rows did not survive'):
            check_stopped(live, stopped, 0)
        stopped['rows'].update({'current:2': 'revised', 'current:3': 'new'})
        stopped['current_foreground_segments'] = 3
        check_stopped(live, stopped, 0)

    def test_old_foreground_records_do_not_validate_new_unknown_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'activity.sqlite3'
            db = sqlite3.connect(path)
            try:
                db.executescript('CREATE TABLE checkpoint(payload TEXT);'
                                 'CREATE TABLE segments(id TEXT,payload TEXT);'
                                 'CREATE TABLE sessions(id TEXT);')
                db.execute('INSERT INTO checkpoint VALUES (?)', (json.dumps({'session': 'new'}),))
                db.executemany('INSERT INTO segments VALUES (?,?)', [
                    ('old:1', json.dumps({'app': 'example.exe', 'start': 0, 'end': 5})),
                    ('new:1', json.dumps({'app': None, 'start': 0, 'end': 5}))])
                db.commit()
            finally:
                db.close()
            result = read_snapshot(path)
            self.assertEqual(result['foreground_segments'], 1)
            self.assertEqual(result['current_foreground_segments'], 0)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows job lifetime')
    def test_abrupt_driver_death_kills_synthetic_descendant(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        code = ('from tools.verify_lifecycle import own_job; import subprocess,sys,time; '
                'own_job(); p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"], '
                'creationflags=subprocess.CREATE_NO_WINDOW); print(p.pid,flush=True); time.sleep(30)')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'pid.txt'
            with output.open('wb') as stream:
                parent = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=stream, stderr=stream)
                child = None
                try:
                    deadline = time.monotonic() + 5
                    while not output.read_text().strip():
                        self.assertIsNone(parent.poll())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.05)
                    child = kernel.OpenProcess(0x100001, False, int(output.read_text().strip()))
                    self.assertTrue(child)
                    self.assertEqual(kernel.WaitForSingleObject(child, 0), 258)
                    parent.kill()
                    parent.wait(timeout=5)
                    self.assertEqual(kernel.WaitForSingleObject(child, 5000), 0)
                finally:
                    if parent.poll() is None:
                        parent.kill()
                        parent.wait(timeout=5)
                    if child:
                        if kernel.WaitForSingleObject(child, 0) != 0:
                            kernel.TerminateProcess(child, 1)
                            kernel.WaitForSingleObject(child, 5000)
                        kernel.CloseHandle(child)

    def test_missing_database_is_not_created_by_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'absent.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                read_snapshot(path)
            self.assertFalse(path.exists())
