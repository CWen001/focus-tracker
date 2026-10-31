"""Strict, bounded local protocol. Never deserialize Python objects."""
import json
import struct
from .timeline import Observation, validate

MAX_FRAME = 16384
FIELDS = {'v', 'generation', 'seq', 'focused', 'tab', 'document', 'domain', 'video_id', 'title', 'playback'}


class ProtocolError(Exception):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('DUPLICATE_KEY')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ProtocolError('INVALID_NUMBER')


def read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = stream.read(length-len(chunks))
        if not part:
            raise ProtocolError('TRUNCATED_FRAME')
        chunks.extend(part)
    return bytes(chunks)


def read_frame(stream):
    first = stream.read(1)
    if not first:
        return None
    length = struct.unpack('<I', first+read_exact(stream, 3))[0]
    if not 1 <= length <= MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    try:
        return json.loads(read_exact(stream, length).decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError('INVALID_JSON') from None


def write_frame(stream, value):
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError('INVALID_JSON') from None
    if not 1 <= len(raw) <= MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    stream.write(struct.pack('<I', len(raw))+raw)
    stream.flush()


def validate_message(value):
    import re
    if type(value) is not dict or value.keys() != FIELDS:
        raise ProtocolError('INVALID_FIELDS')
    if type(value['v']) is not int or value['v'] != 1:
        raise ProtocolError('VERSION')
    if not isinstance(value['generation'], str) or not re.fullmatch('[a-f0-9]{32}', value['generation']):
        raise ProtocolError('GENERATION')
    if type(value['seq']) is not int or not 0 <= value['seq'] < 2**53:
        raise ProtocolError('INVALID_SEQUENCE')
    if type(value['focused']) is not bool:
        raise ProtocolError('INVALID_FOCUS')
    tab, doc = value['tab'], value['document']
    if tab is not None and (type(tab) is not int or not 0 <= tab < 2**53):
        raise ProtocolError('INVALID_TAB')
    if doc is not None and (not isinstance(doc, str) or not 1 <= len(doc) <= 128 or any(ord(c) < 32 or ord(c) > 126 for c in doc)):
        raise ProtocolError('INVALID_DOCUMENT')
    if not value['focused'] and (any(value[key] is not None for key in ('tab', 'document', 'domain', 'video_id', 'title')) or value['playback'] != 'unknown'):
        raise ProtocolError('UNFOCUSED_IDENTITY')
    if value['focused'] and tab is None:
        raise ProtocolError('INVALID_TAB')
    try:
        validate(Observation(0, 0, 0, 'chrome.exe', 0, value['domain'], value['video_id'], value['title'], value['playback']))
    except ValueError:
        raise ProtocolError('INVALID_METADATA') from None
    if doc is None and value['playback'] != 'unknown':
        raise ProtocolError('UNVERIFIED_PLAYBACK')
    return value


class Channel:
    """Caller serializes access; only the current connection's latest seq is retained."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.value = None
        self.received_at = None

    def accept(self, value, now):
        validate_message(value)
        if self.value is not None:
            if value['generation'] != self.value['generation']:
                raise ProtocolError('GENERATION')
            if value['seq'] < self.value['seq']:
                raise ProtocolError('STALE')
            if value['seq'] == self.value['seq']:
                if value != self.value:
                    raise ProtocolError('CONFLICT')
                return 'duplicate'
        self.value = value.copy()
        self.received_at = now
        return 'accepted'

    def snapshot(self, now):
        if self.received_at is None or not 0 <= now-self.received_at <= 3:
            return None
        return self.value.copy()
