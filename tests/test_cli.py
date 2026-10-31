"""Observable CLI behavior; no real activity or registry changes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from activity_log.__main__ import data_path_check, host_check

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, env=None):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'activity_log', *args],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=10,
    )


class CLITests(unittest.TestCase):
    def test_help_succeeds_without_environment_checks(self):
        result = cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('doctor', result.stdout)
        self.assertEqual(result.stderr, '')
        self.assertNotIn('HOST_', result.stdout)

    def test_run_without_permission_does_not_create_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data'
            result=cli('run','--data-dir',str(path))
            self.assertEqual(result.returncode,2)
            self.assertIn('OBSERVATION_NOT_AUTHORIZED',result.stderr)
            self.assertFalse(path.exists())

    def test_missing_status_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=cli('status','--data-dir',tmp)
            self.assertEqual(result.returncode,3)
            self.assertIn('MISSING_DATABASE',result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_status_outputs_timestamped_facts_not_current_health(self):
        from activity_log.storage import Store
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp)/'activity.sqlite3') as store:
                store.commit('one',[],dict(session='test',utc=1000,at=0,connection='unknown',stopped=True))
            result=cli('status','--data-dir',tmp)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('LAST_CHECKPOINT',result.stdout)
            self.assertIn('SAVED_SESSION test',result.stdout)
            self.assertIn('not current process health',result.stdout)
            self.assertNotIn('RUNNING',result.stdout)

    def test_bad_arguments_are_not_echoed(self):
        result = cli('doctor', '--secret-input=private-token')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ERROR INVALID_ARGUMENT', result.stderr)
        self.assertNotIn('private-token', result.stderr + result.stdout)

    def test_relative_data_path_is_rejected_without_creation(self):
        result = cli('doctor', '--data-dir', 'not-a-real-data-folder')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ERROR INVALID_ARGUMENT', result.stderr)
        self.assertFalse((ROOT / 'not-a-real-data-folder').exists())

    def test_doctor_does_not_create_data_or_change_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sentinel = base / 'existing.sqlite3'
            sentinel.write_bytes(b'not a database; must remain untouched')
            result = cli('doctor', '--data-dir', str(base / 'data'))
            self.assertIn(result.returncode, (0, 1), result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertIn('LIMITATION', result.stdout)
            self.assertEqual(list(base.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b'not a database; must remain untouched')

    def test_missing_localappdata_does_not_fall_back_to_project(self):
        env = os.environ.copy()
        env.pop('LOCALAPPDATA', None)
        result = cli('doctor', env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn('MISSING DATA_ROOT_MISSING', result.stdout)
        self.assertFalse((ROOT / 'activity.sqlite3').exists())


class DiagnosticBoundaryTests(unittest.TestCase):
    def test_project_data_is_not_accepted(self):
        result = data_path_check(ROOT / 'records')
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertEqual(result.code, 'DATA_PATH_PROJECT')

    def test_cloud_path_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'OneDrive': tmp}):
                result = data_path_check(Path(tmp) / 'records')
            self.assertEqual(result.code, 'DATA_PATH_SYNCED')
            self.assertFalse((Path(tmp) / 'records').exists())

    def test_network_path_is_rejected_before_any_access(self):
        with patch.object(Path, 'resolve', side_effect=AssertionError('network access attempted')):
            result = data_path_check(Path('//example.invalid/share/data'))
        self.assertEqual(result.code, 'DATA_PATH_UNSAFE')

    def test_file_cannot_be_a_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            path.write_text('preserve', encoding='utf-8')
            result = data_path_check(path)
            self.assertEqual(result.code, 'DATA_PATH_NOT_DIRECTORY')
            self.assertEqual(path.read_text(encoding='utf-8'), 'preserve')

    def test_path_inspection_failure_is_unknown_and_redacted(self):
        with patch.object(Path, 'resolve', side_effect=PermissionError('sensitive-path')):
            result = data_path_check(Path(tempfile.gettempdir()))
        self.assertEqual(result.code, 'CHECK_UNAVAILABLE')
        self.assertNotIn('sensitive-path', result.message)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows registry boundary')
    def test_registry_permission_error_is_not_missing_or_leaked(self):
        with patch('winreg.OpenKey', side_effect=PermissionError('private-registry-value')):
            result = host_check()
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertEqual(result.code, 'CHECK_UNAVAILABLE')
        self.assertNotIn('private-registry-value', result.message)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction support')
    def test_junction_data_path_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'
            target.mkdir()
            link = Path(tmp) / 'link'
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                     capture_output=True, timeout=10)
            self.assertEqual(created.returncode, 0)
            try:
                result = data_path_check(link / 'data')
                self.assertEqual(result.code, 'DATA_PATH_REPARSE')
                self.assertFalse((target / 'data').exists())
            finally:
                link.rmdir()


if __name__ == '__main__':
    unittest.main()
