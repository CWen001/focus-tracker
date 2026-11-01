import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from tools.verify_lifecycle import enable_ctrl_c, read_snapshot


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

    def test_missing_database_is_not_created_by_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'absent.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                read_snapshot(path)
            self.assertFalse(path.exists())
