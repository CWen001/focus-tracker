"""Read-only diagnostics/status and explicitly authorized local collection."""
import argparse
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys
from typing import Literal

HOST_NAME = "com.local.distraction_tracker"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    state: Literal["OK", "MISSING", "UNKNOWN"]
    code: str
    message: str


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "ERROR INVALID_ARGUMENT Invalid command or arguments; see --help.\n")


def data_path_check(path: Path | None) -> Check:
    if path is None:
        return Check("MISSING", "DATA_ROOT_MISSING", "LOCALAPPDATA is not configured.")
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        return Check("UNKNOWN", "DATA_PATH_UNSAFE", "An absolute local path is required.")
    # Reject device/ADS paths before inspecting the filesystem.
    if os.name == "nt" and any(":" in part for part in path.parts[1:]):
        return Check("UNKNOWN", "DATA_PATH_UNSAFE", "Special path syntax is not supported.")
    try:
        if os.name == "nt":
            drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
            drive_type.argtypes = [ctypes.c_wchar_p]
            drive_type.restype = ctypes.c_uint
            if drive_type(path.anchor) not in (2, 3, 5, 6):
                return Check("UNKNOWN", "DATA_PATH_UNSAFE", "A known local drive is required.")
        resolved = path.resolve()
        if str(resolved).startswith((chr(92)*2, "//")):
            return Check("UNKNOWN", "DATA_PATH_UNSAFE", "Resolved network paths are not allowed.")
        if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
            return Check("UNKNOWN", "DATA_PATH_PROJECT", "Activity data must stay outside source.")
        cloud_roots = [os.environ.get(key) for key in
                       ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
        for root in filter(None, cloud_roots):
            cloud = Path(root).resolve()
            if resolved == cloud or cloud in resolved.parents:
                return Check("UNKNOWN", "DATA_PATH_SYNCED", "Known cloud-synced path is not allowed.")
        for parent in (path, *path.parents):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return Check("UNKNOWN", "DATA_PATH_REPARSE", "Redirected path needs manual review.")
            if not stat.S_ISDIR(info.st_mode):
                return Check("UNKNOWN", "DATA_PATH_NOT_DIRECTORY", "Data path or ancestor is not a directory.")
        return Check("OK", "DATA_PATH_CANDIDATE", "No known path risk found; nothing created.")
    except (OSError, ValueError, RuntimeError):
        return Check("UNKNOWN", "CHECK_UNAVAILABLE", "Data path could not be inspected.")


def chrome_check() -> Check:
    roots = [os.environ.get(key) for key in
             ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    try:
        for root in filter(None, roots):
            candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
            if candidate.is_file():
                return Check("OK", "CHROME_FOUND", "Chrome file found; compatibility is unverified.")
    except (OSError, ValueError):
        return Check("UNKNOWN", "CHECK_UNAVAILABLE", "Chrome file could not be inspected.")
    return Check("MISSING", "CHROME_NOT_FOUND", "Chrome not found in standard install locations.")


def host_check() -> Check:
    if sys.platform != "win32":
        return Check("UNKNOWN", "HOST_UNSUPPORTED", "Native host inspection requires Windows.")
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME,
            0, winreg.KEY_READ,
        ) as key:
            value, kind = winreg.QueryValueEx(key, "")
        if kind != winreg.REG_SZ or not isinstance(value, str) or not value:
            return Check("UNKNOWN", "HOST_CONFIG_INVALID", "Native host registration has an invalid value.")
        manifest = Path(value)
        if not manifest.is_absolute() or value.startswith(("\\\\", "//")):
            return Check("UNKNOWN", "HOST_CONFIG_INVALID", "Native host registration is not a local absolute path.")
        if not manifest.is_file():
            return Check("MISSING", "HOST_MANIFEST_MISSING", "Registered manifest file is missing.")
        return Check("UNKNOWN", "HOST_UNVERIFIED", "Registration exists; manifest and connection need validation.")
    except FileNotFoundError:
        return Check("MISSING", "HOST_NOT_CONFIGURED", "Native host is not registered for this user.")
    except (OSError, ValueError):
        return Check("UNKNOWN", "CHECK_UNAVAILABLE", "Native host registration could not be inspected.")


def doctor(data_dir: Path | None) -> int:
    checks = [
        Check("OK" if sys.platform == "win32" else "MISSING",
              "WINDOWS_FOUND" if sys.platform == "win32" else "WINDOWS_REQUIRED",
              "Windows is required for collection."),
        Check("OK" if sys.version_info >= (3, 13) else "MISSING",
              "PYTHON_SUPPORTED" if sys.version_info >= (3, 13) else "PYTHON_TOO_OLD",
              "Python 3.13 or newer is required."),
        data_path_check(data_dir), chrome_check(), host_check(),
    ]
    for check in checks:
        print(f"{check.state} {check.code} {check.message}")
    print("LIMITATION Read-only checks do not prove write access, absence of all sync software, "
          "extension loading, or collection capability. No activity was observed.")
    return 0 if all(check.state == "OK" for check in checks) else 1


def main(argv=None) -> int:
    parser = Parser(description="Local foreground tracker. Collection requires explicit opt-in.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [('doctor', 'Read-only environment checks.'), ('status', 'Read-only saved facts, not current health.'), ('run', 'Collect until Ctrl+C; writes local history.')]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--data-dir', type=Path, help='Absolute local data directory.')
        if name == 'run':
            command.add_argument('--allow-observation', action='store_true', help='Consent to foreground/input observation and local persistence.')
    args = parser.parse_args(argv)
    if args.data_dir is not None and not args.data_dir.is_absolute():
        parser.error("relative path")
    root = os.environ.get("LOCALAPPDATA")
    data_dir = args.data_dir if args.data_dir is not None else (Path(root) / "DistractionTracker" if root else None)
    if args.command == 'doctor':
        return doctor(data_dir)
    if args.command == 'run' and not args.allow_observation:
        print('ERROR OBSERVATION_NOT_AUTHORIZED Explicit --allow-observation is required.', file=sys.stderr)
        return 2
    check = data_path_check(data_dir)
    if check.state != 'OK':
        print(f'ERROR {check.code} {check.message}', file=sys.stderr)
        return 3
    from .storage import StorageError, status
    from .windows import Windows, ProbeError, AlreadyRunning
    from .pipe import PipeServer, PipeError
    try:
        path = data_dir / 'activity.sqlite3'
        if args.command == 'status':
            from datetime import datetime, timezone
            result = status(path)
            checkpoint = result['checkpoint']
            stamp = datetime.fromtimestamp(checkpoint['utc'], timezone.utc).isoformat() if checkpoint else 'none'
            print(f'LAST_CHECKPOINT {stamp}; saved facts, not current process health.')
            if checkpoint:
                print('SAVED_SESSION', checkpoint['session'])
                print('SAVED_STOPPED', checkpoint['stopped'], 'BROWSER', checkpoint['connection'])
            for state, seconds in sorted(result['durations'].items()):
                print(f'DURATION {state} {seconds:.3f}s')
            print('UNOBSERVED_GAPS', len(result['gaps']))
            print('LIMITATION Time after checkpoint is unobserved; pending is not finalized; no complete-day claim.')
            return 0
        from .collector import collect
        windows = Windows()
        with windows.single_instance():
            data_dir.mkdir(parents=True, exist_ok=True)
            with PipeServer() as bridge:
                return collect(path, windows, bridge)
    except AlreadyRunning:
        print('ERROR ALREADY_RUNNING', file=sys.stderr)
        return 4
    except (StorageError, ProbeError, PipeError) as error:
        print('ERROR', str(error), file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError, KeyError):
        print('ERROR RUNTIME_FAILED No raw error details logged; last persisted checkpoint remains authoritative.', file=sys.stderr)
        return 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("ERROR INTERRUPTED Interrupted outside collection loop; inspect saved checkpoint.", file=sys.stderr)
        raise SystemExit(130)
