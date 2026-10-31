"""Synthetic time and disposable databases; never capture the desktop."""
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from activity_log.timeline import Timeline, Observation
from activity_log.storage import Store, status
from activity_log.pipe import PipeServer, PipeClient
from activity_log.protocol import write_frame, read_frame
from tools.native_host import relay
from test_protocol import message


class RecoveryTests(unittest.TestCase):
    def test_eight_virtual_hours_conserve_duration_after_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'activity.sqlite3'
            timeline=Timeline('synthetic')
            with Store(path) as store:
                for t in range(8*3600+1):
                    timeline.observe(Observation(t,1700000000+t,480,'chrome.exe',t*1000,
                        'example.com',playback='playing' if t%1200<600 else 'not-playing'))
                    if t%60==0:
                        store.commit(str(t),timeline.changes(),dict(session='synthetic',utc=1700000000+t,at=t,connection='fresh',stopped=False))
                        timeline.committed()
            saved=status(path)
            self.assertAlmostEqual(sum(saved['durations'].values()),8*3600)
            self.assertGreater(saved['durations']['activity'],14000)
            self.assertGreater(saved['durations']['idle'],14000)
            self.assertEqual(saved['checkpoint']['at'],8*3600)
            self.assertFalse(saved['gaps'])

    def test_native_relay_real_pipe_and_sqlite_only_keep_allowed_fields(self):
        name='DistractionTracker.Test.'+uuid4().hex
        incoming=io.BytesIO();outgoing=io.BytesIO()
        write_frame(incoming,message());incoming.seek(0)
        with tempfile.TemporaryDirectory() as directory, PipeServer(name) as server:
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network not allowed')):
                with PipeClient(name) as client:
                    relay(incoming,outgoing,client)
                    observed=server.snapshot()
                    timeline=Timeline('bridge')
                    for t in (0,1):
                        timeline.observe(Observation(t,1000+t,0,'chrome.exe',0,observed['domain'],observed['video_id'],observed['title'],observed['playback']))
                    path=Path(directory)/'activity.sqlite3'
                    with Store(path) as store:
                        store.commit('one',timeline.changes(),dict(session='bridge',utc=1001,at=1,connection='fresh',stopped=False))
                        payload=json.loads(store.connection.execute('SELECT payload FROM segments').fetchone()[0])
                    self.assertEqual(payload['domain'],'www.youtube.com')
                    self.assertNotIn('generation',payload)
                    self.assertNotIn('document',payload)
                    self.assertNotIn('tab',payload)
                    self.assertEqual(sum(status(path)['durations'].values()),1)
        outgoing.seek(0)
        self.assertEqual(read_frame(outgoing),{'v':1,'status':'accepted'})
