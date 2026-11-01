"""Logon-restricted local pipe. One bounded-message peer, cancellable overlapped I/O."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
from .windows import _bind
from .protocol import Channel, ProtocolError, read_frame, write_frame


class PipeError(Exception):
    pass


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Sid', W.LPVOID), ('Attributes', W.DWORD)]


class TOKEN_GROUPS(C.Structure):
    _fields_ = [('GroupCount', W.DWORD), ('Groups', SID_AND_ATTRIBUTES*1)]


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [('nLength', W.DWORD), ('lpSecurityDescriptor', W.LPVOID), ('bInheritHandle', W.BOOL)]


class OVERLAPPED(C.Structure):
    _fields_ = [('Internal', C.c_size_t), ('InternalHigh', C.c_size_t),
                ('Offset', W.DWORD), ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]


def api():
    k = C.WinDLL('kernel32', use_last_error=True)
    a = C.WinDLL('advapi32', use_last_error=True)
    p = C.POINTER
    _bind(k, 'GetCurrentProcess', W.HANDLE)
    _bind(k, 'CloseHandle', W.BOOL, W.HANDLE)
    _bind(k, 'LocalFree', W.HANDLE, W.HANDLE)
    _bind(a, 'OpenProcessToken', W.BOOL, W.HANDLE, W.DWORD, p(W.HANDLE))
    _bind(a, 'GetTokenInformation', W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD, p(W.DWORD))
    _bind(a, 'ConvertSidToStringSidW', W.BOOL, W.LPVOID, p(W.LPWSTR))
    _bind(a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', W.BOOL, W.LPCWSTR, W.DWORD, p(W.LPVOID), p(W.DWORD))
    _bind(a, 'ConvertSecurityDescriptorToStringSecurityDescriptorW', W.BOOL, W.LPVOID, W.DWORD, W.DWORD, p(W.LPWSTR), p(W.DWORD))
    _bind(a, 'GetSecurityInfo', W.DWORD, W.HANDLE, C.c_int, W.DWORD, W.LPVOID, W.LPVOID, W.LPVOID, W.LPVOID, p(W.LPVOID))
    _bind(k, 'CreateNamedPipeW', W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, p(SECURITY_ATTRIBUTES))
    _bind(k, 'CreateFileW', W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE)
    _bind(k, 'CreateEventW', W.HANDLE, W.LPVOID, W.BOOL, W.BOOL, W.LPCWSTR)
    _bind(k, 'ConnectNamedPipe', W.BOOL, W.HANDLE, p(OVERLAPPED))
    _bind(k, 'DisconnectNamedPipe', W.BOOL, W.HANDLE)
    _bind(k, 'ReadFile', W.BOOL, W.HANDLE, W.LPVOID, W.DWORD, p(W.DWORD), p(OVERLAPPED))
    _bind(k, 'WriteFile', W.BOOL, W.HANDLE, W.LPCVOID, W.DWORD, p(W.DWORD), p(OVERLAPPED))
    _bind(k, 'GetOverlappedResult', W.BOOL, W.HANDLE, p(OVERLAPPED), p(W.DWORD), W.BOOL)
    _bind(k, 'CancelIoEx', W.BOOL, W.HANDLE, p(OVERLAPPED))
    _bind(k, 'WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)
    return k, a


def logon_sid():
    k, a = api()
    token = W.HANDLE()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 8, C.byref(token)):
        raise PipeError('TOKEN_UNAVAILABLE')
    try:
        needed = W.DWORD()
        a.GetTokenInformation(token, 2, None, 0, C.byref(needed))
        if not 1 <= needed.value <= 1024*1024:
            raise PipeError('TOKEN_UNAVAILABLE')
        buffer = C.create_string_buffer(needed.value)
        if not a.GetTokenInformation(token, 2, buffer, needed, C.byref(needed)):
            raise PipeError('TOKEN_UNAVAILABLE')
        count = C.cast(buffer, C.POINTER(TOKEN_GROUPS)).contents.GroupCount
        offset = TOKEN_GROUPS.Groups.offset
        if offset+count*C.sizeof(SID_AND_ATTRIBUTES) > needed.value:
            raise PipeError('TOKEN_UNAVAILABLE')
        groups = (SID_AND_ATTRIBUTES*count).from_buffer(buffer, offset)
        for group in groups:
            if group.Attributes & 0xC0000000 == 0xC0000000:
                value = W.LPWSTR()
                if not a.ConvertSidToStringSidW(group.Sid, C.byref(value)):
                    raise PipeError('SID_UNAVAILABLE')
                try:
                    return value.value
                finally:
                    k.LocalFree(C.cast(value, W.HANDLE))
        raise PipeError('LOGON_SID_MISSING')
    finally:
        k.CloseHandle(token)


def pipe_name(name=None):
    return '\\\\.\\pipe\\'+(name or 'DistractionTracker.'+logon_sid())


class PipeClient:
    def __init__(self, name=None):
        self.k, _ = api()
        self.handle = self.k.CreateFileW(pipe_name(name), 0xC0000000, 0, None, 3, 0, None)
        if self.handle == C.c_void_p(-1).value:
            raise PipeError('COLLECTOR_UNAVAILABLE')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.k.CloseHandle(self.handle)

    def read(self, length):
        buffer = C.create_string_buffer(length)
        count = W.DWORD()
        if not self.k.ReadFile(self.handle, buffer, length, C.byref(count), None):
            if C.get_last_error() in (109, 232, 233):
                return b''
            raise PipeError('PIPE_READ_FAILED')
        return buffer.raw[:count.value]

    def write(self, raw):
        count = W.DWORD()
        if not self.k.WriteFile(self.handle, raw, len(raw), C.byref(count), None) or count.value != len(raw):
            raise PipeError('PIPE_WRITE_FAILED')

    def flush(self):
        pass


class PipeServer:
    def __init__(self, name=None):
        self.k, self.a = api()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.channel = Channel()
        self.error = None
        self.thread = None
        descriptor = W.LPVOID()
        sddl = 'D:P(A;;GA;;;'+logon_sid()+')'
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None):
            raise PipeError('PIPE_SECURITY_FAILED')
        try:
            attributes = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
            self.handle = self.k.CreateNamedPipeW(pipe_name(name), 3 | 0x40000000 | 0x80000, 8, 1, 65536, 65536, 0, C.byref(attributes))
        finally:
            self.k.LocalFree(descriptor)
        if self.handle == C.c_void_p(-1).value:
            raise PipeError('PIPE_CREATE_FAILED')

    def __enter__(self):
        self.thread = threading.Thread(target=self._serve, name='local-browser-pipe', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(2)
            if self.thread.is_alive():
                raise PipeError('PIPE_STOP_FAILED')
        if self.handle is not None:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def _operation(self, function, *args, connect=False):
        event = self.k.CreateEventW(None, True, False, None)
        if not event:
            raise PipeError('PIPE_EVENT_FAILED')
        overlapped = OVERLAPPED(hEvent=event)
        count = W.DWORD()
        try:
            if self.stop.is_set():
                raise PipeError('PIPE_STOPPED')
            ok = function(self.handle, *args, C.byref(overlapped))
            error = C.get_last_error()
            if not ok and connect and error == 535:
                return 0
            if not ok and error != 997:
                raise PipeError('PIPE_DISCONNECTED')
            if not ok:
                deadline = time.monotonic()+5
                while self.k.WaitForSingleObject(event, 50) == 258:
                    if self.stop.is_set() or (not connect and time.monotonic() > deadline):
                        self.k.CancelIoEx(self.handle, C.byref(overlapped))
                        # Buffers and OVERLAPPED stay alive until cancellation completes.
                        self.k.GetOverlappedResult(self.handle, C.byref(overlapped), C.byref(count), True)
                        raise PipeError('PIPE_TIMEOUT')
            if not self.k.GetOverlappedResult(self.handle, C.byref(overlapped), C.byref(count), False):
                raise PipeError('PIPE_DISCONNECTED')
            return count.value
        finally:
            self.k.CloseHandle(event)

    def read(self, length):
        buffer = C.create_string_buffer(length)
        count = W.DWORD()
        n = self._operation(self.k.ReadFile, buffer, length, C.byref(count))
        return buffer.raw[:n]

    def write(self, raw):
        count = W.DWORD()
        if self._operation(self.k.WriteFile, raw, len(raw), C.byref(count)) != len(raw):
            raise PipeError('PIPE_WRITE_FAILED')

    def flush(self):
        pass

    def _serve(self):
        try:
            while not self.stop.is_set():
                try:
                    self._operation(self.k.ConnectNamedPipe, connect=True)
                    while not self.stop.is_set():
                        value = read_frame(self)
                        if value is None:
                            break
                        with self.lock:
                            result = self.channel.accept(value, time.monotonic())
                        write_frame(self, {'v': 1, 'status': result})
                except (ProtocolError, PipeError):
                    pass
                finally:
                    with self.lock:
                        self.channel.reset()
                    self.k.DisconnectNamedPipe(self.handle)
        except Exception:
            self.error = 'PIPE_SERVER_FAILED'
            with self.lock:
                self.channel.reset()

    def snapshot(self):
        with self.lock:
            return self.channel.snapshot(time.monotonic())

    def security_sddl(self):
        descriptor, text = W.LPVOID(), W.LPWSTR()
        if self.a.GetSecurityInfo(self.handle, 6, 4, None, None, None, None, C.byref(descriptor)):
            raise PipeError('PIPE_SECURITY_UNVERIFIED')
        try:
            if not self.a.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, C.byref(text), None):
                raise PipeError('PIPE_SECURITY_UNVERIFIED')
            try:
                return text.value
            finally:
                self.k.LocalFree(C.cast(text, W.HANDLE))
        finally:
            self.k.LocalFree(descriptor)
