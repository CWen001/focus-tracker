import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows process ownership')
class FactoryProcessTests(unittest.TestCase):
    def test_owned_descendants_exit_but_unrelated_process_survives(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        code = ('from tools.verify_lifecycle import own_job; import os,subprocess,sys,time; '
                'from pathlib import Path; own_job(); '
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"], '
                'creationflags=subprocess.CREATE_NO_WINDOW); print(p.pid,flush=True); '
                '\nwhile not Path(sys.argv[1]).exists(): time.sleep(.01)'
                '\nos._exit(23)')
        for role in ('work', 'review'):
            for outcome in ('cancel', 'timeout', 'crash'):
                with self.subTest(role=role, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)
                    output, release = path / 'pid.txt', path / 'crash'
                    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                                 creationflags=subprocess.CREATE_NO_WINDOW)
                    parent = None
                    child = None
                    try:
                        with output.open('wb') as stream:
                            parent = subprocess.Popen([sys.executable, '-B', '-c', code, str(release)],
                                                      stdout=stream, stderr=stream,
                                                      creationflags=subprocess.CREATE_NO_WINDOW)
                            deadline = time.monotonic() + 5
                            while not output.read_text().strip():
                                self.assertIsNone(parent.poll())
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(.02)
                            child = kernel.OpenProcess(0x100001, False, int(output.read_text().strip()))
                            self.assertTrue(child)
                            self.assertEqual(kernel.WaitForSingleObject(child, 0), 258)
                            if outcome == 'crash':
                                release.touch()
                                self.assertEqual(parent.wait(timeout=5), 23)
                            else:
                                if outcome == 'timeout':
                                    with self.assertRaises(subprocess.TimeoutExpired):
                                        parent.wait(timeout=.05)
                                parent.kill()
                                parent.wait(timeout=5)
                            self.assertEqual(kernel.WaitForSingleObject(child, 5000), 0)
                            self.assertIsNone(unrelated.poll())
                    finally:
                        for process in (parent, unrelated):
                            if process and process.poll() is None:
                                process.kill()
                                process.wait(timeout=5)
                        if child:
                            if kernel.WaitForSingleObject(child, 0) != 0:
                                kernel.TerminateProcess(child, 1)
                                kernel.WaitForSingleObject(child, 5000)
                            kernel.CloseHandle(child)
