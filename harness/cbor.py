"""Minimal CBOR codec (RFC 8949 subset) for the LoRa transport.

Spec v2 §"CBOR, not JSON, to minimize bytes" — the wire format over the
radio is CBOR; JSON stays the in-process format (meshd). This module
encodes/decodes exactly what mesh messages contain: dicts, lists,
strings, byte strings, ints, floats, bool/null. No tags, no bignum, no
indefinite lengths. Shortest-form length encoding on write; any
well-formed length accepted on read (decoder-side tolerance, encoder is
canonical). Stdlib-only by repo convention.
"""
from __future__ import annotations

import struct

# major types
_UINT, _NINT, _BYTES, _TEXT, _LIST, _MAP, _TAG, _SIMPLE = range(8)


def _head(major: int, val: int) -> bytes:
    """Shortest-form head for a major type + argument."""
    if val < 24:
        return bytes([(major << 5) | val])
    if val < 0x100:
        return bytes([(major << 5) | 24, val])
    if val < 0x10000:
        return bytes([(major << 5) | 25]) + val.to_bytes(2, "big")
    if val < 0x100000000:
        return bytes([(major << 5) | 26]) + val.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + val.to_bytes(8, "big")


def dumps(obj) -> bytes:
    out = bytearray()
    _enc(obj, out)
    return bytes(out)


def _enc(obj, out: bytearray):
    if obj is False:
        out.append((7 << 5) | 20)
    elif obj is True:
        out.append((7 << 5) | 21)
    elif obj is None:
        out.append((7 << 5) | 22)
    elif isinstance(obj, int):
        if obj >= 0:
            out += _head(_UINT, obj)
        else:
            out += _head(_NINT, -1 - obj)
    elif isinstance(obj, float):
        out += bytes([(7 << 5) | 27]) + struct.pack(">d", obj)
    elif isinstance(obj, str):
        b = obj.encode()
        out += _head(_TEXT, len(b)) + b
    elif isinstance(obj, (bytes, bytearray)):
        out += _head(_BYTES, len(obj)) + bytes(obj)
    elif isinstance(obj, (list, tuple)):
        out += _head(_LIST, len(obj))
        for v in obj:
            _enc(v, out)
    elif isinstance(obj, dict):
        out += _head(_MAP, len(obj))
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("cbor: map keys must be str, got %r" % (k,))
            _enc(k, out)
            _enc(v, out)
    else:
        raise TypeError("cbor: unsupported type %s" % type(obj).__name__)


class _Dec:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def _take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise ValueError("cbor: truncated")
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def _head(self) -> tuple[int, int]:
        b = self._take(1)[0]
        major, ai = b >> 5, b & 0x1F
        if ai < 24:
            return major, ai
        n = {24: 1, 25: 2, 26: 4, 27: 8}.get(ai)
        if n is None:
            raise ValueError("cbor: indefinite lengths not supported")
        return major, int.from_bytes(self._take(n), "big")

    def item(self):
        if self._take(1)[0] == 0xFB:  # major 7, ai 27: float64 (head IS the value)
            return struct.unpack(">d", self._take(8))[0]
        self.i -= 1
        major, val = self._head()
        if major == _UINT:
            return val
        if major == _NINT:
            return -1 - val
        if major == _BYTES:
            return self._take(val)
        if major == _TEXT:
            return self._take(val).decode()
        if major == _LIST:
            return [self.item() for _ in range(val)]
        if major == _MAP:
            m = {}
            for _ in range(val):
                k = self.item()
                if not isinstance(k, str):
                    raise ValueError("cbor: map key not a text string")
                m[k] = self.item()
            return m
        if major == _SIMPLE:
            if val == 20:
                return False
            if val == 21:
                return True
            if val == 22:
                return None
            if val == 27:  # float64
                return struct.unpack(">d", self._take(8))[0]
            raise ValueError("cbor: simple value %d not supported" % val)
        raise ValueError("cbor: major type %d not supported" % major)


def loads(data: bytes):
    d = _Dec(data)
    obj = d.item()
    if d.i != len(d.d):
        raise ValueError("cbor: %d trailing bytes" % (len(d.d) - d.i))
    return obj
