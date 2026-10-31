import json
from pathlib import Path
import tempfile
import unittest
from tools.check_saved_fields import audit_database
from activity_log.storage import Store
from activity_log.timeline import Timeline, Observation


class PrivacyTests(unittest.TestCase):
    def test_allowed_payload_passes_and_extra_private_field_fails_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'activity.sqlite3'
            timeline=Timeline('test')
            for t in (0,1): timeline.observe(Observation(t,1000+t,0,'editor.exe',0))
            with Store(path) as store:
                store.commit('one',timeline.changes(),dict(session='test',utc=1001,at=1,connection='unknown',stopped=True))
                self.assertEqual(audit_database(path),{'rows':1,'violations':0})
                payload=json.loads(store.connection.execute('SELECT payload FROM segments').fetchone()[0])
                payload['private-url']='https://secret.test/token'
                store.connection.execute('UPDATE segments SET payload=?',(json.dumps(payload),))
            result=audit_database(path)
            self.assertEqual(result['violations'],1)
            self.assertNotIn('secret',str(result))

    def test_missing_database_is_not_created(self):
        from activity_log.storage import StorageError
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'absent.sqlite3'
            with self.assertRaises(StorageError): audit_database(path)
            self.assertFalse(path.exists())
