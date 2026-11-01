"""Chrome native host: transport only; no activity database or observation calls."""
from pathlib import Path
import os
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from activity_log.__main__ import Parser
from activity_log.pipe import PipeClient, PipeError
from activity_log.protocol import ProtocolError, read_frame, write_frame, validate_message


def relay(source, destination, client):
    while (value := read_frame(source)) is not None:
        validate_message(value)
        write_frame(client, value)
        reply = read_frame(client)
        if not isinstance(reply, dict) or reply.keys() != {'v', 'status'} or reply['v'] != 1 or reply['status'] not in ('accepted', 'duplicate'):
            raise ProtocolError('INVALID_ACK')
        write_frame(destination, reply)


def main(argv=None):
    parser = Parser(description='Chrome-owned local transport; not a collector.')
    parser.add_argument('--extension-id', required=True)
    parser.add_argument('--origin', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch('[a-p]{32}', args.extension_id) or args.origin != 'chrome-extension://'+args.extension_id+'/':
        print('ERROR ORIGIN_REJECTED', file=sys.stderr)
        return 2
    try:
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
        msvcrt.setmode(1, os.O_BINARY)
        with PipeClient() as client:
            relay(sys.stdin.buffer, sys.stdout.buffer, client)
        return 0
    except (OSError, PipeError, ProtocolError):
        print('ERROR NATIVE_TRANSPORT_FAILED', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
