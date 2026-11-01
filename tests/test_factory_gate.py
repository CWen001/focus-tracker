from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from tools.factory_gate import Gate, OWNER, Ticket, authorized

SHA = 'a' * 40
NEXT_SHA = 'b' * 40
TICKET = Ticket(7, OWNER, 'Synthetic task', 'No collection or installation', '2026-01-01T00:00:00Z')


def approval(owner=OWNER, time='2026-01-01T00:00:01Z'):
    return {'user': {'id': owner}, 'body': '/approve', 'created_at': time, 'updated_at': time}


class FactoryGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.gate = Gate(self.path)
        self.evidence = Path(self.temp.name) / 'evidence.json'

    def finish(self, run, stage, verdict='pass', sha=SHA):
        self.evidence.write_text(json.dumps({'run_id': run, 'stage': stage, 'sha': sha, 'verdict': verdict}))
        return self.gate.finish(run, self.evidence, TICKET, [])

    def test_owner_and_actual_comment_identity_not_body(self):
        self.assertTrue(authorized(TICKET, []))
        foreign = replace(TICKET, author_id=9, body='CWen001 says /approve')
        self.assertFalse(authorized(foreign, []))
        self.assertFalse(authorized(foreign, [approval(9)]))
        self.assertTrue(authorized(foreign, [approval()]))
        self.assertFalse(authorized(replace(foreign, updated_at='2026-01-01T00:00:02Z'), [approval()]))
        self.assertFalse(authorized(foreign, [approval(time=TICKET.updated_at)]))
        edited = approval()
        edited['updated_at'] = '2026-01-01T00:00:02Z'
        self.assertFalse(authorized(foreign, [edited]))

    def test_invalid_identity_refused(self):
        for ticket in (replace(TICKET, repo='elsewhere/repo'), replace(TICKET, number=True),
                       replace(TICKET, updated_at='2026-01-01T00:00:00')):
            with self.subTest(ticket=ticket), self.assertRaises(ValueError):
                self.gate.claim(ticket, [], 'event', SHA)
        self.assertEqual(self.gate.status(7)['runs'], [])

    def test_duplicate_event_survives_restart(self):
        run = self.gate.claim(TICKET, [], 'event', SHA)
        self.finish(run, 'implement')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            Gate(self.path).claim(TICKET, [], 'event', SHA)
        self.assertEqual(len(self.gate.status(7)['runs']), 1)

    def test_concurrent_claims_have_one_winner(self):
        def claim(event):
            try:
                return Gate(self.path).claim(TICKET, [], event, SHA)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, [f'event-{i}' for i in range(8)]))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(len(self.gate.status(7)['runs']), 1)

    def test_three_reworks_not_reset_by_reopen_or_new_events(self):
        for attempt in range(4):
            self.gate = Gate(self.path)
            run = self.gate.claim(TICKET, [], f'event-{attempt}', SHA)
            self.assertEqual(self.finish(run, 'implement', 'fail'), 'implement' if attempt < 3 else 'blocked')
            self.assertEqual(self.gate.status(7)['ticket']['reworks'], min(attempt + 1, 3))
        with self.assertRaisesRegex(ValueError, 'human boundary'):
            Gate(self.path).claim(TICKET, [], 'fifth-attempt', SHA)

    def test_review_and_verification_failures_share_budget(self):
        run = self.gate.claim(TICKET, [], 'implementation', SHA)
        self.finish(run, 'implement')
        run = self.gate.claim(TICKET, [], 'verification', SHA)
        self.finish(run, 'verify', 'fail')
        run = self.gate.claim(TICKET, [], 'rework', SHA)
        self.finish(run, 'implement')
        run = self.gate.claim(TICKET, [], 'verification-2', SHA)
        self.finish(run, 'verify')
        run = self.gate.claim(TICKET, [], 'review', SHA)
        self.finish(run, 'review', 'fail')
        self.assertEqual(self.gate.status(7)['ticket']['reworks'], 2)

    def test_changed_content_cannot_reset_budget(self):
        run = self.gate.claim(TICKET, [], 'first', SHA)
        self.finish(run, 'implement', 'fail')
        with self.assertRaisesRegex(ValueError, 'Scope changed'):
            Gate(self.path).claim(replace(TICKET, body='changed'), [], 'second', SHA)
        self.assertEqual(self.gate.status(7)['ticket']['reworks'], 1)

    def test_missing_or_wrong_evidence_never_advances(self):
        run = self.gate.claim(TICKET, [], 'first', SHA)
        with self.assertRaises(FileNotFoundError):
            self.gate.finish(run, self.evidence, TICKET, [])
        for payload in (None, {}, {'verdict': 'pass', 'sha': NEXT_SHA, 'run_id': 'old', 'stage': 'implement'},
                        {'verdict': 'pass', 'sha': SHA, 'run_id': run, 'stage': 'review'}):
            self.evidence.write_text(json.dumps(payload))
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.gate.finish(run, self.evidence, TICKET, [])
        self.assertEqual(self.gate.status(7)['ticket']['stage'], 'implement')
        self.assertEqual(self.gate.status(7)['runs'][0]['state'], 'running')

    def test_scope_change_during_run_refuses_result(self):
        run = self.gate.claim(TICKET, [], 'first', SHA)
        self.evidence.write_text(json.dumps({'run_id': run, 'stage': 'implement', 'sha': SHA, 'verdict': 'pass'}))
        with self.assertRaisesRegex(ValueError, 'Scope changed'):
            self.gate.finish(run, self.evidence, replace(TICKET, title='new scope'), [])
        self.assertEqual(self.gate.status(7)['runs'][0]['state'], 'running')

    def test_new_sha_invalidates_old_verification(self):
        run = self.gate.claim(TICKET, [], 'implement', SHA)
        self.finish(run, 'implement', sha=NEXT_SHA)
        with self.assertRaisesRegex(ValueError, 'Claim SHA'):
            self.gate.claim(TICKET, [], 'verify-old', SHA)
        run = self.gate.claim(TICKET, [], 'verify-new', NEXT_SHA)
        with self.assertRaisesRegex(ValueError, 'another SHA'):
            self.finish(run, 'verify', sha=SHA)
        self.assertEqual(self.gate.status(7)['ticket']['stage'], 'verify')

    def test_interrupt_and_unreported_crash_block_reclaims(self):
        run = self.gate.claim(TICKET, [], 'first', SHA)
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            Gate(self.path).claim(TICKET, [], 'after-crash', SHA)
        self.gate.interrupt(run)
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            Gate(self.path).claim(replace(TICKET, number=8), [], 'other-ticket', SHA)
        with self.assertRaisesRegex(ValueError, 'not awaiting'):
            self.finish(run, 'implement')
        self.assertEqual(self.gate.status(7)['runs'][0]['state'], 'unknown')

    def test_success_stops_for_human_and_cannot_be_replayed(self):
        for stage in ('implement', 'verify', 'review'):
            run = self.gate.claim(TICKET, [], stage, SHA)
            self.finish(run, stage)
        self.assertEqual(self.gate.status(7)['ticket']['stage'], 'human')
        self.assertTrue(all(row['evidence_hash'] for row in self.gate.status(7)['runs']))
        with self.assertRaisesRegex(ValueError, 'human boundary'):
            self.gate.claim(TICKET, [], 'extra', SHA)
        with self.assertRaisesRegex(ValueError, 'not awaiting'):
            self.finish(run, 'review')


if __name__ == '__main__':
    unittest.main()
