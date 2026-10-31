import io
import unittest
from unittest.mock import patch
from activity_log.protocol import write_frame, read_frame
from tools.native_host import relay, main
from test_protocol import message


class NativeHostTests(unittest.TestCase):
    def test_wrong_origin_rejected_before_pipe(self):
        with patch('tools.native_host.PipeClient') as client:
            self.assertEqual(main(['--extension-id','a'*32,'--origin','chrome-extension://'+'b'*32+'/']),2)
            client.assert_not_called()

    def test_relay_validates_and_acknowledges(self):
        incoming=io.BytesIO(); outgoing=io.BytesIO(); received=[]
        write_frame(incoming,message()); incoming.seek(0)
        class Fake:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def write(self,raw): received.append(raw)
            def flush(self): pass
            def read(self,n): return ack.read(n)
        ack=io.BytesIO(); write_frame(ack,{'v':1,'status':'accepted'}); ack.seek(0)
        relay(incoming,outgoing,Fake())
        outgoing.seek(0)
        self.assertEqual(read_frame(outgoing)['status'],'accepted')
        self.assertEqual(read_frame(io.BytesIO(b''.join(received))),message())

    def test_invalid_payload_never_reaches_pipe(self):
        incoming=io.BytesIO(); write_frame(incoming,{'raw_url':'private'}); incoming.seek(0)
        from activity_log.protocol import ProtocolError
        with patch('tools.native_host.write_frame') as send:
            with self.assertRaises(ProtocolError): relay(incoming,io.BytesIO(),object())
            send.assert_not_called()
