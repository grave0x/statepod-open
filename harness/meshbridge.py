"""meshbridge -- ctypes binding to the shared C mesh base (libmesh.a).

The C mesh (~/Projects/02-tools/mesh) is the stateless CRDT core: Lamport
ordering, dedup, deterministic fold.  This module gives the StatePod
harness a thin Python face on that core so the harness can provide the
*transport* (Tailcat / LoRa / plaintext-LAN) via the send callback.

Wire format: JSON op dicts (same as agent-mesh).
  {"op": <kind>, "target": "...", "value": "...",
   "clock": {"counter": n, "origin": "..."}}
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os

from pathlib import Path

# The C mesh installs a static libmesh.a; ctypes needs a shared object.
# Build it from the shared base (same source) with:
#   gcc -std=c99 -O2 -fPIC -shared -Iinclude src/mesh.c \
#       -o ~/.local/lib/libmesh.so
LIB = ctypes.CDLL(os.environ.get(
    "STATEPOD_MESH_LIB",
    str(Path.home() / ".local" / "lib" / "libmesh.so")))

# ── constants (must match mesh.h) ────────────────────────────────────────
MESH_OP_SET = 0
MESH_OP_DEL = 1
MESH_OP_INC = 2
MESH_OP_APP = 3

KIND_NAME = {"set": MESH_OP_SET, "del": MESH_OP_DEL,
             "inc": MESH_OP_INC, "app": MESH_OP_APP}


class MeshClock(ctypes.Structure):
    _fields_ = [("counter", ctypes.c_uint64),
                ("origin", ctypes.c_char * 32)]


class MeshOp(ctypes.Structure):
    _fields_ = [("clock", MeshClock),
                ("kind", ctypes.c_uint8),
                ("target", ctypes.c_char * 96),
                ("value", ctypes.c_char * 160)]


# opaque peer handle
class MeshPeer(ctypes.Structure):
    pass


SEND_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p,
                           ctypes.c_size_t)

# ── function signatures ──────────────────────────────────────────────────
_mesh_peer_new = LIB.mesh_peer_new
_mesh_peer_new.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
_mesh_peer_new.restype = ctypes.c_void_p

_mesh_peer_free = LIB.mesh_peer_free
_mesh_peer_free.argtypes = [ctypes.c_void_p]
_mesh_peer_free.restype = None

_mesh_peer_mutate = LIB.mesh_peer_mutate
_mesh_peer_mutate.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                              ctypes.c_char_p, ctypes.c_char_p]
_mesh_peer_mutate.restype = ctypes.c_int

_mesh_peer_apply_json = LIB.mesh_peer_apply_json
_mesh_peer_apply_json.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                  ctypes.c_size_t]
_mesh_peer_apply_json.restype = ctypes.c_int

_mesh_peer_state_json = LIB.mesh_peer_state_json
_mesh_peer_state_json.argtypes = [ctypes.c_void_p]
_mesh_peer_state_json.restype = ctypes.c_void_p

_mesh_peer_latest = LIB.mesh_peer_latest
_mesh_peer_latest.argtypes = [ctypes.c_void_p]
_mesh_peer_latest.restype = MeshClock

_mesh_peer_set_send = LIB.mesh_peer_set_send
_mesh_peer_set_send.argtypes = [ctypes.c_void_p, SEND_FN, ctypes.c_void_p]
_mesh_peer_set_send.restype = None

_mesh_peer_stats = LIB.mesh_peer_stats
_mesh_peer_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                             ctypes.POINTER(ctypes.c_size_t),
                             ctypes.POINTER(ctypes.c_size_t)]
_mesh_peer_stats.restype = None

_mesh_free = LIB.mesh_free
_mesh_free.argtypes = [ctypes.c_void_p]
_mesh_free.restype = None


class Peer:
    """One C-mesh peer.  `send` is the transport callback: a callable
    receiving (json_bytes) that ships the op to every peer."""

    def __init__(self, peer_id: str, ring_bits: int = 12, send=None):
        self.peer_id = peer_id
        self._send_cb = SEND_FN(self._on_send) if send is not None else None
        self.send = send
        self._h = _mesh_peer_new(peer_id.encode(), ring_bits)
        if not self._h:
            raise RuntimeError("mesh_peer_new failed")
        # keep the callback alive for the lifetime of the peer
        if self._send_cb is not None:
            _mesh_peer_set_send(self._h, self._send_cb, None)

    def _on_send(self, _ud, buf, length):
        data = ctypes.string_at(buf, length)
        if self.send is not None:
            self.send(data)

    def mutate(self, kind, target: str, value: str) -> bool:
        return bool(_mesh_peer_mutate(self._h, kind, target.encode(),
                                      value.encode()))

    def apply_json(self, blob: bytes | str) -> bool:
        if isinstance(blob, str):
            blob = blob.encode()
        return bool(_mesh_peer_apply_json(self._h, blob, len(blob)))

    def state_json(self) -> str:
        ptr = _mesh_peer_state_json(self._h)
        if not ptr:
            return "{}"
        try:
            return ctypes.string_at(ptr).decode()
        finally:
            _mesh_free(ptr)

    def latest(self) -> dict:
        clk = _mesh_peer_latest(self._h)
        return {"counter": clk.counter,
                "origin": clk.origin.decode(errors="replace").rstrip("\\x00")}

    def stats(self) -> dict:
        tail = ctypes.c_size_t()
        compacted = ctypes.c_size_t()
        rss = ctypes.c_size_t()
        _mesh_peer_stats(self._h, ctypes.byref(tail), ctypes.byref(compacted),
                         ctypes.byref(rss))
        return {"tail_ops": tail.value, "compacted": compacted.value,
                "rss_kb": rss.value}

    def close(self):
        if self._h:
            _mesh_peer_free(self._h)
            self._h = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def op_json(peer_id: str, kind: str, target: str, value: str,
            counter: int = 0) -> str:
    """A hand-rolled op dict for the wire (matches mesh.h's JSON parser)."""
    return json.dumps({"op": kind, "target": target, "value": value,
                       "clock": {"counter": counter, "origin": peer_id}},
                      separators=(",", ":"))
