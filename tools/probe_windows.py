"""Bounded, explicitly opted-in Windows probe; never writes activity history."""
from pathlib import PureWindowsPath
import sys
import time

from activity_log.__main__ import Parser
from activity_log.windows import AlreadyRunning, ProbeError, Windows


def main(argv=None) -> int:
    parser = Parser(description='Windows observation probe; requires separate experiment permission.')
    parser.add_argument('--allow-observation', action='store_true', help='Explicitly enable real foreground/input reads.')
    parser.add_argument('--seconds', type=int, default=60, help='Duration, 1 to 600 seconds.')
    parser.add_argument('--apps', nargs='+', default=['notepad.exe', 'calc.exe'],
                        help='Executable basenames mapped to target-1, target-2, etc.; other names are never printed.')
    args = parser.parse_args(argv)
    if not args.allow_observation:
        print('ERROR OBSERVATION_NOT_AUTHORIZED Explicit observation opt-in is required.', file=sys.stderr)
        return 2
    if not 1 <= args.seconds <= 600:
        parser.error('duration out of range')
    if any(PureWindowsPath(app).name != app or not app.lower().endswith('.exe')
           or any(char in app for char in '\\/:\n\r\t') for app in args.apps):
        parser.error('expected basenames')
    aliases = {app.lower(): f'target-{i + 1}' for i, app in enumerate(args.apps)}
    try:
        api = Windows()
        with api.single_instance(), api.notifications() as poll:
            start = time.monotonic()
            next_sample = start
            print('PROBE_START Memory-only observation; initial session state unknown.', flush=True)
            while time.monotonic() - start < args.seconds:
                now = time.monotonic()
                for event in poll():
                    print(f'EVENT {now - start:.3f} {event}', flush=True)
                if now >= next_sample:
                    foreground = api.foreground()
                    alias = aliases.get(foreground.app, 'other') if foreground.app is not None else 'unknown'
                    idle_ms = api.input_idle_ms()
                    print(f'SAMPLE {now - start:.3f} {alias} {foreground.reason} '
                          f'{idle_ms if idle_ms is not None else "unknown"}', flush=True)
                    next_sample = now + 1
                time.sleep(0.1)
        print('PROBE_STOP No activity history saved.', flush=True)
        return 0
    except AlreadyRunning:
        print('ERROR ALREADY_RUNNING Another collector owns this session lock.', file=sys.stderr)
        return 4
    except ProbeError as error:
        print(f'ERROR {error} Windows probe failed; no history saved.', file=sys.stderr)
        return 3
    except (OSError, ValueError):
        print('ERROR PROBE_FAILED Windows probe failed; no history saved.', file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print('ERROR INTERRUPTED Probe stopped; no history saved.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
