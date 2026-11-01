"""Opt-in Windows CLI lifecycle proof with a fresh local database."""
import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_snapshot(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        checkpoint = db.execute('SELECT payload FROM checkpoint').fetchone()
        rows = db.execute('SELECT id,payload FROM segments').fetchall()
        sessions = db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
    segments = [json.loads(payload) for _, payload in rows]
    checkpoint = json.loads(checkpoint[0]) if checkpoint else None
    current = checkpoint['session'] + ':' if checkpoint else None
    return {'checkpoint': checkpoint,
            'current_foreground_segments': sum(key.startswith(current) and segment.get('app') is not None
                                               for (key, _), segment in zip(rows, segments)) if current else 0,
            'rows': {key: hashlib.sha256(payload.encode()).hexdigest() for key, payload in rows},
            'sessions': sessions,
            'foreground_segments': sum(s.get('app') is not None for s in segments),
            'seconds': sum(s['end'] - s['start'] for s in segments)}


def own_job():
    class Limits(ctypes.Structure):
        _fields_ = [('per_process', ctypes.c_int64), ('per_job', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                    ('max_working', ctypes.c_size_t), ('active', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', Limits), ('io', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    require(job, 'Cannot create lifecycle job')
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # Kill descendants when the driver's non-inherited handle closes.
    try:
        require(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)), 'Cannot set job limit')
        require(kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()), 'Cannot protect driver descendants')
    except BaseException:
        kernel.CloseHandle(job)
        raise
    # Keep this handle open until OS process teardown; closing it would also terminate the driver.
    return job


def require_no_browser_host():
    import winreg
    from activity_log.__main__ import HOST_NAME
    key = r'Software\Google\Chrome\NativeMessagingHosts' + chr(92) + HOST_NAME
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view):
                    raise RuntimeError('Registered Chrome host exists; preserve it and stop this recipe')
            except FileNotFoundError:
                continue
            except OSError as error:
                raise RuntimeError('Cannot inspect all Chrome host registrations') from error


def enable_ctrl_c():
    # Agent shells can pass an ignore-Ctrl+C flag to children, even in a new console.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    require(kernel.SetConsoleCtrlHandler(None, False), 'Cannot enable Ctrl+C inheritance')


def interrupt(pid):
    # CTRL_C_EVENT cannot target a process group; join only our child's private console.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.FreeConsole.argtypes = []
    kernel.FreeConsole.restype = wintypes.BOOL
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel.FreeConsole()
    require(kernel.AttachConsole(pid), 'Cannot attach to owned console')
    try:
        require(kernel.SetConsoleCtrlHandler(None, True), 'Cannot protect signal helper')
        require(kernel.GenerateConsoleCtrlEvent(0, 0), 'Cannot send Ctrl+C')
        time.sleep(.2)
    finally:
        kernel.FreeConsole()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-observation', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_observation:
        print('ERROR OBSERVATION_NOT_AUTHORIZED', file=sys.stderr)
        return 2
    if sys.platform != 'win32':
        print('ERROR WINDOWS_REQUIRED', file=sys.stderr)
        return 2
    from activity_log.__main__ import data_path_check
    require_no_browser_host()
    parent = Path(os.environ['LOCALAPPDATA'])
    require(data_path_check(parent).state == 'OK', 'Unsafe verification root')
    run = Path(tempfile.mkdtemp(prefix='FocusTracker-verify-', dir=parent))
    data = run / 'data'
    database = data / 'activity.sqlite3'
    report = {'verdict': 'FAIL', 'cycles': [], 'commands': [],
              'scope': 'CLI lifecycle only; no Chrome, idle threshold, or lock/sleep proof'}
    owned = []
    started = time.monotonic()
    timed_out = threading.Event()

    def kill_owned():
        for process in tuple(owned):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def watchdog():
        timed_out.set()
        kill_owned()

    timer = threading.Timer(60, watchdog)
    timer.daemon = True

    def command(arguments):
        completed = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=5)
        report['commands'].append({'args': arguments, 'exit': completed.returncode,
                                   'stdout': completed.stdout, 'stderr': completed.stderr})
        return completed

    def status():
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        result = command([sys.executable, '-B', '-m', 'activity_log', 'status', '--data-dir', str(data)])
        require(result.returncode == 0 and 'SAVED_STOPPED True' in result.stdout, 'Stopped status not confirmed')
        require(hashlib.sha256(database.read_bytes()).hexdigest() == before, 'Status modified the stopped database')

    timer.start()
    try:
        own_job()
        enable_ctrl_c()
        report['base_sha'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        report['source_hashes'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for directory in ('activity_log', 'tools') for p in sorted((ROOT / directory).glob('*.py'))}
        before = command([sys.executable, '-B', '-m', 'activity_log', 'status', '--data-dir', str(data)])
        require(before.returncode == 3 and 'MISSING_DATABASE' in before.stderr and not data.exists(),
                'Empty-directory status was not read-only')
        previous = None
        for cycle in range(2):
            require(not timed_out.is_set(), 'Verification deadline exceeded')
            require_no_browser_host()
            output = run / f'collector-{cycle}.txt'
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            with output.open('wb') as stream:
                process = subprocess.Popen([sys.executable, '-u', '-B', '-m', 'activity_log', 'run',
                                            '--data-dir', str(data), '--allow-observation'], cwd=ROOT,
                                           stdout=stream, stderr=stream,
                                           creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
                owned.append(process)
                deadline = time.monotonic() + 18
                while True:
                    require(process.poll() is None, 'Collector exited before a live checkpoint')
                    require(time.monotonic() < deadline and not timed_out.is_set(), 'No live checkpoint before deadline')
                    text = output.read_text(encoding='utf-8')
                    if 'CHECKPOINT Saved' in text:
                        live = read_snapshot(database)
                        if live['checkpoint'] and live['checkpoint']['at'] >= 5:
                            break
                    time.sleep(.1)
                require(live['checkpoint']['stopped'] is False, 'Live checkpoint incorrectly stopped')
                require(live['current_foreground_segments'] > 0, 'No foreground observation saved in the current session')
                helper = command([sys.executable, '-B', '-c',
                                  'from tools.verify_lifecycle import interrupt; import sys; interrupt(int(sys.argv[1]))',
                                  str(process.pid)])
                require(helper.returncode == 0, 'Ctrl+C delivery failed')
                exit_code = process.wait(timeout=5)
            require(exit_code == 130 and 'STOPPED Saved' in output.read_text(encoding='utf-8'), 'Graceful stop failed')
            stopped = read_snapshot(database)
            require(stopped['checkpoint']['stopped'] is True, 'Stopped checkpoint not saved')
            require(stopped['checkpoint']['session'] == live['checkpoint']['session'], 'Wrong session stopped')
            require(stopped['sessions'] == cycle + 1, 'Unexpected session count')
            status()
            if previous:
                require(stopped['checkpoint']['session'] != previous['checkpoint']['session'], 'Restart reused a session')
                require(all(stopped['rows'].get(key) == value for key, value in previous['rows'].items()),
                        'Restart changed or lost prior records')
            report['cycles'].append({'pid': process.pid, 'exit': exit_code, 'live': live, 'stopped': stopped})
            previous = stopped
        require(not timed_out.is_set(), 'Verification deadline exceeded')
        report['verdict'] = 'PASS'
    except Exception as error:
        report['error'] = str(error)
    finally:
        try:
            kill_owned()
        finally:
            timer.cancel()
            timer.join()
            report['elapsed_seconds'] = time.monotonic() - started
            report['all_owned_exited'] = all(p.poll() is not None for p in owned)
            if not report['all_owned_exited'] or timed_out.is_set():
                report['verdict'] = 'FAIL'
            evidence = run / 'result.json'
            evidence.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(report['verdict'], evidence)
    return 0 if report['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
