import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from activity_log.timeline import Timeline, Observation
from activity_log.storage import Store, StorageError, status


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'activity.sqlite3'
        t=Timeline('session')
        t.observe(Observation(0,1000,0,'editor.exe',0))
        t.observe(Observation(1,1001,0,'editor.exe',1000))
        self.segments=t.changes()
        self.checkpoint={'session':'session','utc':1001,'at':1,'connection':'unknown','stopped':False}

    def test_missing_status_creates_nothing(self):
        with self.assertRaisesRegex(StorageError,'MISSING_DATABASE'): status(self.path)
        self.assertFalse(self.path.exists())

    def test_commit_restart_duplicate_conflict_and_revision(self):
        with Store(self.path) as store:
            self.assertEqual(store.commit('c1',self.segments,self.checkpoint),'accepted')
            self.assertEqual(store.commit('c1',self.segments,self.checkpoint),'duplicate')
            with self.assertRaisesRegex(StorageError,'CONFLICT'):
                store.commit('c1',[],self.checkpoint)
        with Store(self.path) as store:
            self.assertEqual(store.commit('c1',self.segments,self.checkpoint),'duplicate')
            revised=[replace(self.segments[0],revision=2,state='idle')]
            store.commit('c2',revised,self.checkpoint)
            with self.assertRaisesRegex(StorageError,'STALE_REVISION'):
                store.commit('c3',self.segments,self.checkpoint)
        result=status(self.path)
        self.assertEqual(result['durations'],{'idle':1.0})
        self.assertEqual(result['checkpoint'],self.checkpoint)

    def test_transaction_failure_keeps_old_rows_and_allows_retry(self):
        with Store(self.path) as store:
            store.commit('c1',self.segments,self.checkpoint)
            store.connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON segments BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
            revised=[replace(self.segments[0],revision=2,state='idle')]
            with self.assertRaises(StorageError): store.commit('c2',revised,self.checkpoint)
            self.assertEqual(status(self.path)['durations'],{'pending':1.0})
            store.connection.execute('DROP TRIGGER fail_update')
            self.assertEqual(store.commit('c2',revised,self.checkpoint),'accepted')

    def test_corrupt_existing_file_is_not_recreated(self):
        self.path.write_bytes(b'not a database')
        with self.assertRaises(StorageError): Store(self.path)
        self.assertEqual(self.path.read_bytes(),b'not a database')

    def test_existing_empty_file_is_not_initialized(self):
        self.path.touch()
        with self.assertRaises(StorageError): Store(self.path)
        self.assertEqual(self.path.stat().st_size,0)

    def test_status_does_not_modify_database(self):
        with Store(self.path) as store: store.commit('c1',self.segments,self.checkpoint)
        before=self.path.read_bytes()
        status(self.path)
        self.assertEqual(self.path.read_bytes(),before)

    def test_restart_exposes_unobserved_gap(self):
        with Store(self.path) as store:
            store.commit('c1',self.segments,self.checkpoint)
            store.commit('c2',[],dict(self.checkpoint,session='next',utc=1100,at=0))
        self.assertEqual(status(self.path)['gaps'],[{'start':1001,'end':1100}])

    def test_linked_database_is_rejected_before_sqlite_opens(self):
        from unittest.mock import patch
        with Store(self.path) as store: store.commit('c1',self.segments,self.checkpoint)
        before=self.path.read_bytes()
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(StorageError):
                with Store(self.path): pass
            with self.assertRaises(StorageError): status(self.path)
        self.assertEqual(self.path.read_bytes(),before)

    def test_conflicting_equal_revision_rolls_back(self):
        with Store(self.path) as store:
            store.commit('c1',self.segments,self.checkpoint)
            with self.assertRaisesRegex(StorageError,'CONFLICT'):
                store.commit('c2',[replace(self.segments[0],state='idle')],self.checkpoint)
        self.assertEqual(status(self.path)['durations'],{'pending':1.0})
