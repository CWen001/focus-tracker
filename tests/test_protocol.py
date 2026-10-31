import io
import json
import struct
import unittest
from activity_log.protocol import read_frame, write_frame, validate_message, Channel, ProtocolError


def message(**changes):
    return dict(v=1,generation='a'*32,seq=0,focused=True,tab=1,document='doc',domain='www.youtube.com',video_id='abcdefghijk',title='Study',playback='playing',**{}) | changes


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_and_clean_eof(self):
        stream=io.BytesIO()
        write_frame(stream,message())
        stream.seek(0)
        self.assertEqual(read_frame(stream),message())
        self.assertIsNone(read_frame(stream))

    def test_bad_lengths_truncation_and_duplicate_keys(self):
        for raw in [b'x',struct.pack('<I',16385),struct.pack('<I',3)+b'{}',struct.pack('<I',13)+b'{"v":1,"v":2}']:
            with self.assertRaises(ProtocolError): read_frame(io.BytesIO(raw))

    def test_boundary_metadata_types_and_unknown_fields(self):
        for changes in [dict(v=True),dict(seq=True),dict(seq=-1),dict(seq=2**53),dict(raw_url='secret'),dict(domain='example.com'),dict(title='bad\ntext'),dict(focused=False),dict(document=''),dict(generation='x')]:
            with self.subTest(changes=changes),self.assertRaises(ProtocolError): validate_message(message(**changes))

    def test_duplicate_conflict_stale_and_expiry(self):
        channel=Channel()
        self.assertEqual(channel.accept(message(),10),'accepted')
        self.assertEqual(channel.accept(message(),12),'duplicate')
        self.assertIsNone(channel.snapshot(14))
        with self.assertRaisesRegex(ProtocolError,'CONFLICT'): channel.accept(message(title='changed'),14)
        channel.accept(message(seq=2),15)
        with self.assertRaisesRegex(ProtocolError,'STALE'): channel.accept(message(seq=1),16)
        with self.assertRaisesRegex(ProtocolError,'GENERATION'): channel.accept(message(seq=3,generation='b'*32),17)
        channel.reset()
        self.assertIsNone(channel.snapshot(18))
        self.assertEqual(channel.accept(message(generation='b'*32),18),'accepted')

    def test_unfocused_has_no_identity(self):
        valid=message(focused=False,tab=None,document=None,domain=None,video_id=None,title=None,playback='unknown')
        self.assertEqual(validate_message(valid),valid)
