"""Read-only field audit. Prints counts, never saved activity or private values."""
from dataclasses import fields
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from activity_log.__main__ import Parser, data_path_check
from activity_log.storage import StorageError, check_schema, reject_links
from activity_log.timeline import Segment, Observation, validate


def audit_database(path):
    connection = None
    try:
        reject_links(path)
        if not path.is_file():
            raise StorageError('MISSING_DATABASE')
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        connection.execute('PRAGMA query_only=ON')
        check_schema(connection)
        rows = violations = 0
        allowed = {field.name for field in fields(Segment)}
        for (raw,) in connection.execute('SELECT payload FROM segments'):
            rows += 1
            try:
                s = json.loads(raw)
                if type(s) is not dict or s.keys() != allowed:
                    raise ValueError()
                if type(s['revision']) is not int or s['revision'] < 1 or not isinstance(s['id'], str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}',s['id']):
                    raise ValueError()
                if s['state'] not in ('activity','pending','idle','unknown','locked','suspended'):
                    raise ValueError()
                for key in ('start','end','utc_start','utc_end'):
                    if type(s[key]) not in (int,float) or not math.isfinite(s[key]):
                        raise ValueError()
                if s['end'] < s['start'] or abs((s['end']-s['start'])-(s['utc_end']-s['utc_start'])) > .01:
                    raise ValueError()
                validate(Observation(s['start'],s['utc_start'],s['offset'],s['app'],0,
                    s['domain'],s['video_id'],s['title'],s['playback']))
            except (ValueError, TypeError, KeyError, RecursionError):
                violations += 1
        for (raw,) in connection.execute('SELECT payload FROM checkpoint'):
            try:
                c = json.loads(raw)
                if type(c) is not dict or c.keys() != {'session','utc','at','connection','stopped'}:
                    raise ValueError()
                if not isinstance(c['session'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',c['session']):
                    raise ValueError()
                if c['connection'] not in ('fresh','unknown') or type(c['stopped']) is not bool:
                    raise ValueError()
                if any(type(c[k]) not in (int,float) or not math.isfinite(c[k]) for k in ('utc','at')):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, RecursionError):
                violations += 1
        return {'rows':rows,'violations':violations}
    except (OSError,sqlite3.Error):
        raise StorageError('DATABASE_UNAVAILABLE') from None
    finally:
        if connection is not None:
            connection.close()


def main(argv=None):
    parser=Parser(description='Read-only activity field audit; local paths only, no record output.')
    parser.add_argument('--data-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if data_path_check(args.data_dir).state != 'OK':
            raise StorageError('UNSAFE_DATA_PATH')
        result=audit_database(args.data_dir/'activity.sqlite3')
        print(json.dumps(result))
        return 1 if result['violations'] else 0
    except StorageError:
        print('ERROR AUDIT_UNAVAILABLE',file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
