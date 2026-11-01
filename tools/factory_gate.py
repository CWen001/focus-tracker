"""Offline admission/state probe. No GitHub ingestion, agent dispatch or activity capture."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

OWNER = 30044400
REPO = 'CWen001/focus-tracker'
STAGES = {'implement': 'verify', 'verify': 'review', 'review': 'human'}


@dataclass(frozen=True)
class Ticket:
    number: int
    author_id: int
    title: str
    body: str
    updated_at: str
    repo: str = REPO

    @property
    def version(self):
        return hashlib.sha256(json.dumps([self.repo, self.number, self.author_id, self.title,
                                         self.body, self.updated_at], ensure_ascii=False).encode()).hexdigest()


def authorized(ticket, comments):
    """Inputs must be trusted API snapshots; this function does not authenticate them."""
    updated = datetime.fromisoformat(ticket.updated_at.replace('Z', '+00:00'))
    if ticket.repo != REPO or type(ticket.number) is not int or ticket.number < 1 or updated.tzinfo is None:
        raise ValueError('Invalid ticket identity or timestamp')
    if ticket.author_id == OWNER:
        return True
    for comment in comments:
        if comment.get('user', {}).get('id') != OWNER or comment.get('body', '').strip() != '/approve':
            continue
        created = datetime.fromisoformat(comment['created_at'].replace('Z', '+00:00'))
        if created.tzinfo and created > updated and comment['created_at'] == comment['updated_at']:
            return True
    return False


class Gate:
    def __init__(self, path):
        self.path = Path(path)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tickets (
                    number INTEGER PRIMARY KEY, version TEXT NOT NULL, stage TEXT NOT NULL,
                    sha TEXT NOT NULL, reworks INTEGER NOT NULL DEFAULT 0 CHECK(reworks BETWEEN 0 AND 3));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, event TEXT NOT NULL UNIQUE, ticket INTEGER NOT NULL,
                    version TEXT NOT NULL, stage TEXT NOT NULL, sha TEXT NOT NULL,
                    state TEXT NOT NULL, evidence_hash TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active ON runs((1))
                    WHERE state IN ('running', 'unknown');
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, ticket, comments, event, sha):
        if not authorized(ticket, comments):
            raise ValueError('Ticket is not authorized')
        if not isinstance(event, str) or not event or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Invalid event or SHA')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM runs WHERE event=?', (event,)).fetchone():
                raise ValueError('Duplicate event')
            if db.execute("SELECT 1 FROM runs WHERE state IN ('running','unknown')").fetchone():
                raise ValueError('Active or unknown run requires reconciliation')
            row = db.execute('SELECT * FROM tickets WHERE number=?', (ticket.number,)).fetchone()
            if row is None:
                db.execute('INSERT INTO tickets(number,version,stage,sha) VALUES (?,?,?,?)',
                           (ticket.number, ticket.version, 'implement', sha))
                stage = 'implement'
            else:
                if row['version'] != ticket.version:
                    raise ValueError('Scope changed; explicit reconciliation required')
                if row['stage'] not in STAGES:
                    raise ValueError('Ticket is stopped at a human boundary')
                if row['sha'] != sha:
                    raise ValueError('Claim SHA differs from current candidate')
                stage = row['stage']
            run = uuid.uuid4().hex
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,NULL)',
                       (run, event, ticket.number, ticket.version, stage, sha, 'running'))
            return run

    def finish(self, run, evidence, ticket, comments):
        if not authorized(ticket, comments):
            raise ValueError('Ticket is no longer authorized')
        raw = Path(evidence).read_bytes()
        report = json.loads(raw)
        if not isinstance(report, dict) or report.get('verdict') not in ('pass', 'fail'):
            raise ValueError('Evidence must contain a pass/fail verdict')
        sha = report.get('sha', '')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Invalid evidence SHA')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runs WHERE id=?', (run,)).fetchone()
            if row is None or row['state'] != 'running':
                raise ValueError('Run is not awaiting a result')
            if row['ticket'] != ticket.number or row['version'] != ticket.version:
                raise ValueError('Scope changed while the run was active')
            if report.get('run_id') != run or report.get('stage') != row['stage']:
                raise ValueError('Evidence belongs to another run or stage')
            if row['stage'] != 'implement' and sha != row['sha']:
                raise ValueError('Evidence belongs to another SHA')
            ticket = db.execute('SELECT * FROM tickets WHERE number=?', (row['ticket'],)).fetchone()
            reworks = ticket['reworks']
            if report['verdict'] == 'pass':
                stage = STAGES[row['stage']]
            elif reworks < 3:
                stage = 'implement'
                reworks += 1
            else:
                stage = 'blocked'
            db.execute('UPDATE tickets SET stage=?,sha=?,reworks=? WHERE number=?',
                       (stage, sha, reworks, row['ticket']))
            db.execute("UPDATE runs SET state='finished',evidence_hash=? WHERE id=?",
                       (hashlib.sha256(raw).hexdigest(), run))
            return stage

    def interrupt(self, run):
        with self.connection() as db:
            changed = db.execute("UPDATE runs SET state='unknown' WHERE id=? AND state='running'", (run,))
            if changed.rowcount != 1:
                raise ValueError('Run is not active')

    def status(self, number):
        with self.connection() as db:
            ticket = db.execute('SELECT * FROM tickets WHERE number=?', (number,)).fetchone()
            runs = db.execute('SELECT * FROM runs WHERE ticket=? ORDER BY rowid', (number,)).fetchall()
            return {'ticket': dict(ticket) if ticket else None, 'runs': [dict(row) for row in runs]}
