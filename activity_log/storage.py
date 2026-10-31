"""Single-writer SQLite storage with durable, atomic retry identities."""
from dataclasses import asdict
import hashlib
import json
import sqlite3
from pathlib import Path


class StorageError(Exception):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def check_schema(connection):
    if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise StorageError('UNSUPPORTED_DATABASE')
    connection.execute('SELECT id, revision, payload FROM segments LIMIT 0')
    connection.execute('SELECT id, digest FROM commits LIMIT 0')
    connection.execute('SELECT id, payload FROM checkpoint LIMIT 0')
    connection.execute('SELECT id, start, end FROM sessions LIMIT 0')


def reject_links(path):
    for suffix in ('', '-journal', '-wal', '-shm'):
        candidate = Path(str(path)+suffix)
        if candidate.is_symlink():
            raise StorageError('UNSAFE_DATABASE_PATH')
        try:
            if getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400:
                raise StorageError('UNSAFE_DATABASE_PATH')
        except FileNotFoundError:
            pass


class Store:
    def __init__(self, path):
        self.connection = None
        try:
            reject_links(path)
            if path.exists():
                self.connection = sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True, isolation_level=None)
                check_schema(self.connection)
            else:
                # Reserve only this file: never initialize an existing empty/corrupt DB.
                with path.open('xb'):
                    pass
                self.connection = sqlite3.connect(path, isolation_level=None)
                self.connection.executescript('''BEGIN IMMEDIATE;
                    CREATE TABLE segments(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE commits(id TEXT PRIMARY KEY, digest TEXT NOT NULL);
                    CREATE TABLE checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                    CREATE TABLE sessions(id TEXT PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL);
                    PRAGMA user_version=1;
                    COMMIT;''')
        except (OSError, sqlite3.Error, StorageError):
            if self.connection is not None:
                self.connection.close()
            raise StorageError('DATABASE_UNAVAILABLE') from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def commit(self, commit_id, segments, checkpoint):
        payloads = [asdict(s) for s in segments]
        digest = hashlib.sha256(encode([payloads, checkpoint]).encode()).hexdigest()
        c = self.connection
        try:
            c.execute('BEGIN IMMEDIATE')
            prior = c.execute('SELECT digest FROM commits WHERE id=?', (commit_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise StorageError('CONFLICT')
                c.execute('ROLLBACK')
                return 'duplicate'
            c.execute('INSERT INTO commits VALUES (?,?)', (commit_id, digest))
            for segment in payloads:
                serialized = encode(segment)
                prior = c.execute('SELECT revision,payload FROM segments WHERE id=?', (segment['id'],)).fetchone()
                if prior:
                    if prior[0] > segment['revision']:
                        raise StorageError('STALE_REVISION')
                    if prior[0] == segment['revision'] and prior[1] != serialized:
                        raise StorageError('CONFLICT')
                c.execute('INSERT INTO segments VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                          (segment['id'], segment['revision'], serialized))
            c.execute('INSERT INTO checkpoint VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (encode(checkpoint),))
            c.execute('INSERT INTO sessions VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET end=excluded.end',
                      (checkpoint['session'], checkpoint['utc']-checkpoint['at'], checkpoint['utc']))
            c.execute('COMMIT')
            return 'accepted'
        except (sqlite3.Error, StorageError) as error:
            if c.in_transaction:
                c.execute('ROLLBACK')
            if isinstance(error, StorageError):
                raise
            raise StorageError('COMMIT_FAILED') from None


def status(path):
    try:
        reject_links(path)
    except OSError:
        raise StorageError('DATABASE_UNAVAILABLE') from None
    if not path.is_file():
        raise StorageError('MISSING_DATABASE')
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        check_schema(connection)
        row = connection.execute('SELECT payload FROM checkpoint WHERE id=1').fetchone()
        totals = {}
        for (payload,) in connection.execute('SELECT payload FROM segments'):
            segment = json.loads(payload)
            totals[segment['state']] = totals.get(segment['state'], 0)+segment['end']-segment['start']
        sessions = connection.execute('SELECT start,end FROM sessions ORDER BY rowid').fetchall()
        gaps = [{'start': before[1], 'end': after[0]} for before, after in zip(sessions, sessions[1:]) if after[0] > before[1]]
        return {'checkpoint': json.loads(row[0]) if row else None, 'durations': totals, 'gaps': gaps}
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        raise StorageError('DATABASE_UNAVAILABLE') from None
    finally:
        if connection is not None:
            connection.close()
