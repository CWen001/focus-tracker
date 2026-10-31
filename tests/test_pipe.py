import sys
import threading
import time
import unittest
from uuid import uuid4
from activity_log.pipe import PipeServer, PipeClient, PipeError, logon_sid
from activity_log.protocol import write_frame, read_frame
from test_protocol import message


@unittest.skipUnless(sys.platform=='win32','Windows named pipes')
class PipeTests(unittest.TestCase):
    def test_acl_exchange_disconnect_and_cancellation(self):
        name='DistractionTracker.Test.'+uuid4().hex
        with PipeServer(name) as server:
            sid=logon_sid()
            self.assertTrue(sid.startswith('S-1-5-5-'))
            self.assertIn(sid,server.security_sddl())
            self.assertNotIn(';;;WD)',server.security_sddl())
            with PipeClient(name) as client:
                write_frame(client,message())
                self.assertEqual(read_frame(client),{'v':1,'status':'accepted'})
                self.assertEqual(server.snapshot()['title'],'Study')
                write_frame(client,message())
                self.assertEqual(read_frame(client)['status'],'duplicate')
            deadline=time.monotonic()+2
            while server.snapshot() is not None and time.monotonic()<deadline: time.sleep(.01)
            self.assertIsNone(server.snapshot())
        self.assertFalse(server.thread.is_alive())

    def test_first_instance_rejects_second_server(self):
        name='DistractionTracker.Test.'+uuid4().hex
        with PipeServer(name):
            with self.assertRaises(PipeError): PipeServer(name)

    def test_stop_cancels_incomplete_frame(self):
        name='DistractionTracker.Test.'+uuid4().hex
        server=PipeServer(name)
        with server,PipeClient(name) as client:
            client.write(b'\x10')
            server.close()
            self.assertFalse(server.thread.is_alive())

    def test_server_survives_invalid_client_then_new_generation(self):
        name='DistractionTracker.Test.'+uuid4().hex
        with PipeServer(name) as server:
            with PipeClient(name) as client:
                write_frame(client,{'secret':'not accepted'})
                self.assertIsNone(read_frame(client))
            deadline=time.monotonic()+2
            while True:
                try:
                    client=PipeClient(name)
                    break
                except PipeError:
                    if time.monotonic()>deadline: raise
                    time.sleep(.01)
            with client:
                write_frame(client,message(generation='b'*32))
                self.assertEqual(read_frame(client)['status'],'accepted')
            self.assertIsNone(server.error)
