"""Opt-in Win32 probe boundaries; importing this module observes nothing.

API sources and deliberately unverified live behavior: tasks/validation.md.
"""
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import PureWindowsPath
import sys
from typing import Literal
from uuid import uuid4


class ProbeError(Exception):
    """Contains a stable, non-sensitive code, never the native exception text."""


class AlreadyRunning(ProbeError):
    pass


@dataclass(frozen=True)
class Foreground:
    app: str | None
    reason: Literal['known', 'no-window', 'process-unavailable', 'changed-during-sample']


class InputClock:
    def __init__(self):
        self.last_tick = None
        self.last_now = None
        self.last_event_ms = None

    def sample(self, now_ms: int, tick: int) -> int | None:
        if type(now_ms) is not int or type(tick) is not int or now_ms < 0 or not 0 <= tick < 2**32:
            return None
        if self.last_now is not None and now_ms < self.last_now:
            return None
        self.last_now = now_ms
        age = (now_ms - tick) % 2**32
        if tick == self.last_tick and self.last_event_ms is not None:
            age = now_ms - self.last_event_ms
        # A half-cycle or future timestamp is ambiguous, not proof of absence.
        if age >= 2**31 or age > now_ms:
            return None
        if self.last_tick is not None and (tick - self.last_tick) % 2**32 >= 2**31:
            return None
        self.last_tick = tick
        self.last_event_ms = now_ms - age
        return age


def notification_event(message: int, event: int) -> str | None:
    if message == 0x2B1:  # WM_WTSSESSION_CHANGE
        return {7: 'lock', 8: 'unlock'}.get(event, 'session-change')
    if message == 0x218:  # WM_POWERBROADCAST
        return {4: 'suspend', 7: 'resume', 18: 'resume'}.get(event)
    return None


class LASTINPUTINFO(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]


class WTSINFOEX_LEVEL1(C.Structure):
    _fields_ = [('SessionId', W.DWORD), ('SessionState', C.c_int), ('SessionFlags', W.LONG),
                ('WinStationName', W.WCHAR*33), ('UserName', W.WCHAR*21), ('DomainName', W.WCHAR*18),
                ('LogonTime', C.c_longlong), ('ConnectTime', C.c_longlong),
                ('DisconnectTime', C.c_longlong), ('LastInputTime', C.c_longlong), ('CurrentTime', C.c_longlong),
                ('IncomingBytes', W.DWORD), ('OutgoingBytes', W.DWORD),
                ('IncomingFrames', W.DWORD), ('OutgoingFrames', W.DWORD),
                ('IncomingCompressedBytes', W.DWORD), ('OutgoingCompressedBytes', W.DWORD)]


class WTSINFOEX(C.Structure):
    _fields_ = [('Level', W.DWORD), ('Data', WTSINFOEX_LEVEL1)]


WNDPROC = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', C.c_int), ('cbWndExtra', C.c_int),
                ('hInstance', W.HINSTANCE), ('hIcon', W.HANDLE),
                ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH),
                ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]


def _bind(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args


class Windows:
    def __init__(self):
        if sys.platform != 'win32':
            raise ProbeError('WINDOWS_REQUIRED')
        self.user32 = C.WinDLL('user32', use_last_error=True)
        self.kernel32 = C.WinDLL('kernel32', use_last_error=True)
        self.wtsapi32 = C.WinDLL('wtsapi32', use_last_error=True)
        self.input_clock = InputClock()
        u, k, wts = self.user32, self.kernel32, self.wtsapi32
        _bind(u, 'GetForegroundWindow', W.HWND)
        _bind(u, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
        _bind(u, 'GetLastInputInfo', W.BOOL, C.POINTER(LASTINPUTINFO))
        _bind(k, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        _bind(k, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
        _bind(k, 'CloseHandle', W.BOOL, W.HANDLE)
        _bind(k, 'GetTickCount64', C.c_ulonglong)
        _bind(k, 'CreateMutexW', W.HANDLE, W.LPVOID, W.BOOL, W.LPCWSTR)
        _bind(k, 'GetModuleHandleW', W.HMODULE, W.LPCWSTR)
        _bind(u, 'RegisterClassW', W.ATOM, C.POINTER(WNDCLASS))
        _bind(u, 'UnregisterClassW', W.BOOL, W.LPCWSTR, W.HINSTANCE)
        _bind(u, 'CreateWindowExW', W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
              C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
        _bind(u, 'DestroyWindow', W.BOOL, W.HWND)
        _bind(u, 'DefWindowProcW', C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        _bind(u, 'PeekMessageW', W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
        _bind(u, 'TranslateMessage', W.BOOL, C.POINTER(W.MSG))
        _bind(u, 'DispatchMessageW', C.c_ssize_t, C.POINTER(W.MSG))
        _bind(wts, 'WTSRegisterSessionNotification', W.BOOL, W.HWND, W.DWORD)
        _bind(wts, 'WTSUnRegisterSessionNotification', W.BOOL, W.HWND)
        _bind(wts, 'WTSQuerySessionInformationW', W.BOOL, W.HANDLE, W.DWORD, C.c_int, C.POINTER(W.LPVOID), C.POINTER(W.DWORD))
        _bind(wts, 'WTSFreeMemory', None, W.LPVOID)

    def session_state(self) -> str:
        buffer, size = W.LPVOID(), W.DWORD()
        if not self.wtsapi32.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, C.byref(buffer), C.byref(size)):
            return 'unknown'
        try:
            if not buffer or size.value < C.sizeof(WTSINFOEX):
                return 'unknown'
            info = C.cast(buffer, C.POINTER(WTSINFOEX)).contents
            if info.Level != 1 or info.Data.SessionState != 0:
                return 'unknown'
            # Only flags leave this boundary; names and timestamps are never read or persisted.
            return {0: 'locked', 1: 'active'}.get(info.Data.SessionFlags, 'unknown')
        finally:
            self.wtsapi32.WTSFreeMemory(buffer)

    def foreground(self) -> Foreground:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return Foreground(None, 'no-window')
        pid = W.DWORD()
        if not self.user32.GetWindowThreadProcessId(hwnd, C.byref(pid)) or not pid.value:
            return Foreground(None, 'process-unavailable')
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return Foreground(None, 'process-unavailable')
        try:
            name = C.create_unicode_buffer(32768)
            size = W.DWORD(len(name))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)):
                return Foreground(None, 'process-unavailable')
            if self.user32.GetForegroundWindow() != hwnd:
                return Foreground(None, 'changed-during-sample')
            app = PureWindowsPath(name.value).name.lower()
            if not app:
                return Foreground(None, 'process-unavailable')
            return Foreground(app, 'known')
        finally:
            if not self.kernel32.CloseHandle(handle):
                raise ProbeError('PROCESS_HANDLE_CLOSE_FAILED')

    def input_idle_ms(self) -> int | None:
        info = LASTINPUTINFO(C.sizeof(LASTINPUTINFO), 0)
        if not self.user32.GetLastInputInfo(C.byref(info)):
            return None
        return self.input_clock.sample(self.kernel32.GetTickCount64(), info.dwTime)

    @contextmanager
    def single_instance(self, name: str = 'Local\\DistractionTracker.Collector'):
        C.set_last_error(0)
        handle = self.kernel32.CreateMutexW(None, False, name)
        error = C.get_last_error()
        if not handle:
            raise ProbeError('INSTANCE_LOCK_FAILED')
        try:
            if error == 183:  # ERROR_ALREADY_EXISTS is success-with-an-existing-handle.
                raise AlreadyRunning('ALREADY_RUNNING')
            yield
        finally:
            if not self.kernel32.CloseHandle(handle):
                raise ProbeError('INSTANCE_LOCK_CLOSE_FAILED')

    @contextmanager
    def notifications(self):
        events = []
        u, wts = self.user32, self.wtsapi32

        @WNDPROC
        def callback(hwnd, message, wparam, lparam):
            event = notification_event(message, wparam)
            if event is not None:
                events.append(event)
                return 1 if message == 0x218 else 0
            return u.DefWindowProcW(hwnd, message, wparam, lparam)

        instance = self.kernel32.GetModuleHandleW(None)
        if not instance:
            raise ProbeError('MODULE_HANDLE_FAILED')
        class_name = 'DistractionTracker.Probe.' + uuid4().hex
        window_class = WNDCLASS(lpfnWndProc=callback, hInstance=instance, lpszClassName=class_name)
        if not u.RegisterClassW(C.byref(window_class)):
            raise ProbeError('WINDOW_CLASS_FAILED')
        hwnd = None
        registered = False
        try:
            # A hidden top-level window receives power broadcasts; HWND_MESSAGE does not.
            hwnd = u.CreateWindowExW(0, class_name, '', 0, 0, 0, 0, 0, None, None, instance, None)
            if not hwnd:
                raise ProbeError('WINDOW_CREATE_FAILED')
            if not wts.WTSRegisterSessionNotification(hwnd, 0):
                raise ProbeError('SESSION_NOTIFICATION_FAILED')
            registered = True

            def poll() -> list[str]:
                message = W.MSG()
                while u.PeekMessageW(C.byref(message), hwnd, 0, 0, 1):
                    u.TranslateMessage(C.byref(message))
                    u.DispatchMessageW(C.byref(message))
                result = list(events)
                events.clear()
                return result

            yield poll
        finally:
            clean = True
            if registered:
                clean = bool(wts.WTSUnRegisterSessionNotification(hwnd)) and clean
            if hwnd:
                clean = bool(u.DestroyWindow(hwnd)) and clean
            clean = bool(u.UnregisterClassW(class_name, instance)) and clean
            if not clean:
                raise ProbeError('NOTIFICATION_CLEANUP_FAILED')
