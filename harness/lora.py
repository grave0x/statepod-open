"""LoRa transport simulator (spec v1 section 8: air-gapped/offline).

The mesh transport is a swap shim (tailcat over WireGuard, LoRa over
radio, ...).  This module simulates a REAL narrowband radio link so the
swarm's adaptation layer can be built and tested without hardware:

  - frame cap          real LoRa payloads are ~51-222 bytes
  - airtime            one frame per slot (half-duplex)
  - loss               configurable drop probability; ARQ retransmits
  - stop-and-wait ARQ  per-frame ACK + timeout retransmit
  - CBOR               JSON↔CBOR at the radio boundary (byte-saving)
  - AES-256-GCM        optional PSK encrypt of the CBOR blob (spec §13.3)
  - priority TX        ACKs and high-priority DATA ahead of bulk
  - route cache        per-stream ack/timeout RTT + reliability score
"""
from __future__ import annotations

import json
import os
import random
import socket
import threading
import time
from heapq import heappop, heappush

import cbor as _cbor

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover
    AESGCM = None  # type: ignore

K_DATA, K_ACK = 0, 1
_HDR = 8  # side(1) kind(1) msg_id(2) idx(1) total(1) plen(2)
_ENC_MAGIC = 0xE1
_NONCE_LEN = 12
# TX priorities (lower = sooner). ACKs always win.
P_ACK = 0
P_HIGH = 1   # inference req/resp, state diffs
P_NORM = 5
P_BULK = 9   # registry / heartbeats


def _pack(side: int, kind: int, msg_id: int, idx: int, total: int,
          payload: bytes = b"") -> bytes:
    return (bytes([side, kind]) + msg_id.to_bytes(2, "big")
            + bytes([idx, total]) + len(payload).to_bytes(2, "big")
            + payload)


def _unpack(frame: bytes) -> dict:
    return {
        "side": frame[0], "kind": frame[1],
        "msg_id": int.from_bytes(frame[2:4], "big"),
        "idx": frame[4], "total": frame[5],
        "plen": int.from_bytes(frame[6:8], "big"),
        "payload": frame[_HDR:],
    }


def _psk_from_env_or_arg(psk: bytes | str | None) -> bytes | None:
    if psk is None:
        env = os.environ.get("SP_LORA_PSK", "")
        if not env:
            return None
        psk = env
    if isinstance(psk, str):
        psk = bytes.fromhex(psk) if all(c in "0123456789abcdefABCDEF"
                                        for c in psk) and len(psk) == 64 \
            else psk.encode()
    if len(psk) not in (16, 24, 32):
        raise ValueError("LoRa PSK must be 16/24/32 bytes (AES key)")
    return psk


def _encrypt(psk: bytes, plain: bytes) -> bytes:
    if AESGCM is None:
        raise RuntimeError("cryptography package required for LoRa AES-GCM")
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(psk).encrypt(nonce, plain, None)
    return bytes([_ENC_MAGIC]) + nonce + ct


def _decrypt(psk: bytes, blob: bytes) -> bytes:
    if AESGCM is None:
        raise RuntimeError("cryptography package required for LoRa AES-GCM")
    if len(blob) < 1 + _NONCE_LEN + 16 or blob[0] != _ENC_MAGIC:
        raise ValueError("not an AES-GCM LoRa blob")
    nonce, ct = blob[1:1 + _NONCE_LEN], blob[1 + _NONCE_LEN:]
    return AESGCM(psk).decrypt(nonce, ct, None)


def _priority_for_payload(data: bytes) -> int:
    """Best-effort classify meshd JSON for TX priority (mesh-spec v2)."""
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return P_NORM
    if not isinstance(obj, dict):
        return P_NORM
    t = str(obj.get("type", ""))
    if t in ("req", "resp", "hello", "join"):
        return P_HIGH
    if t in ("lora_share", "op", "append"):
        # ops inside share: prefer if any look like state-critical
        return P_HIGH
    if t in ("capability", "heartbeat"):
        return P_BULK
    return P_NORM


class LoRaLink:
    """The shared half-duplex radio channel."""

    def __init__(self, frame_cap: int = 96, p_loss: float = 0.05,
                 airtime: float = 0.02, seed: int | None = None,
                 log=None, psk: bytes | str | None = None,
                 require_psk: bool | None = None):
        self.frame_cap = max(32, frame_cap)
        self.p_loss = p_loss
        self.airtime = airtime
        self.rng = random.Random(seed)
        self.log = log or (lambda *a: None)
        self.psk = _psk_from_env_or_arg(psk)
        # H3: field mode fail-closed — SP_LORA_REQUIRE_PSK=1 or require_psk=True
        if require_psk is None:
            require_psk = os.environ.get("SP_LORA_REQUIRE_PSK", "") in (
                "1", "true", "yes")
        if require_psk and self.psk is None:
            raise ValueError("LoRa require_psk set but no SP_LORA_PSK/psk")
        # priority queues: list of (prio, seq, frame_dict)
        self._tx: dict[str, list] = {"A": [], "B": []}
        self._tx_seq = 0
        self._rx: dict[str, list] = {"A": [], "B": []}
        self._rbuf: dict[str, dict] = {"A": {}, "B": {}}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.stats = {"frames": 0, "dropped": 0, "retransmits": 0,
                      "payload_bytes": 0, "data_frames": 0,
                      "ack_frames": 0, "airtime_s": 0.0,
                      "cbor_msgs": 0, "enc_msgs": 0, "dec_fail": 0}
        self._thread = threading.Thread(target=self._radio_loop,
                                        daemon=True)
        self._thread.start()

    def _radio_loop(self):
        """Serial half-duplex channel: one frame per airtime slot."""
        order = ["A", "B"]
        i = 0
        while not self._stop.is_set():
            with self._lock:
                side = order[i % 2]
                i += 1
                q = self._tx[side]
                frame = None
                if q:
                    _prio, _seq, frame = heappop(q)
            if frame is not None:
                target = "B" if frame["side"] == 0 else "A"
                self.stats["frames"] += 1
                if self.rng.random() < self.p_loss:
                    self.stats["dropped"] += 1
                    self.log(f"[lora] DROP {frame['kind']} frame "
                             f"m{frame['msg_id']}.{frame['idx']}")
                    continue
                with self._lock:
                    self._rx[target].append(frame)
                if frame["kind"] == K_DATA:
                    ack = _pack(1 - frame["side"], K_ACK,
                                frame["msg_id"], frame["idx"], 0)
                    self._enqueue(target, _unpack(ack), P_ACK)
                self.stats[("ack_frames" if frame["kind"] == K_ACK
                            else "data_frames")] += 1
                self.stats["payload_bytes"] += frame["plen"]
                self.log(f"[lora] {target} "
                         f"{'ack' if frame['kind'] == K_ACK else 'frame'} "
                         f"m{frame['msg_id']}.{frame['idx']}"
                         + (f"/{frame['total']} ({frame['plen']}B)"
                            if frame["kind"] == K_DATA else ""))
                self.stats["airtime_s"] += self.airtime
            self._stop.wait(self.airtime)

    def _enqueue(self, side_name: str, frame: dict, priority: int):
        with self._lock:
            self._tx_seq += 1
            heappush(self._tx[side_name],
                     (priority, self._tx_seq, frame))

    def endpoint(self, name: str) -> "LoRaStream":
        return LoRaStream(self, 0 if name == "A" else 1, name)

    def send(self, side: int, frame: bytes, priority: int = P_NORM):
        side_name = "A" if side == 0 else "B"
        kind = frame[1] if len(frame) > 1 else K_DATA
        prio = P_ACK if kind == K_ACK else priority
        self._enqueue(side_name, _unpack(frame), prio)

    def _pull(self, side: int, kind: int) -> dict | None:
        key = "A" if side == 0 else "B"
        with self._lock:
            for i, f in enumerate(self._rx[key]):
                if f["kind"] == kind:
                    return self._rx[key].pop(i)
        return None

    def next_message(self, side: int, timeout: float) -> tuple[int, bytes] | None:
        deadline = time.monotonic() + timeout
        key = "A" if side == 0 else "B"
        while time.monotonic() < deadline:
            f = self._pull(side, K_DATA)
            if f is not None:
                buf = self._rbuf[key]
                buf.setdefault(f["msg_id"], {})[f["idx"]] = f
                parts = buf[f["msg_id"]]
                total = max(x["total"] for x in parts.values())
                if len(parts) == total:
                    msgs = [parts[i] for i in sorted(parts)]
                    del buf[f["msg_id"]]
                    return f["msg_id"], b"".join(m["payload"] for m in msgs)
            time.sleep(0.005)
        return None

    def next_ack(self, side: int, timeout: float) -> tuple[int, int] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            f = self._pull(side, K_ACK)
            if f is not None:
                return f["msg_id"], f["idx"]
            time.sleep(0.005)
        return None

    def shutdown(self):
        self._stop.set()


class LoRaStream:
    """Socket-like endpoint: stop-and-wait ARQ over the shared radio."""

    def __init__(self, link: LoRaLink, side: int, name: str):
        self.link = link
        self.side = side
        self.name = name
        self._timeout = 0.5
        self._msg_id = 0
        self._acked: dict[int, set] = {}
        self._seen: set[int] = set()
        self._closed = False
        self._lock = threading.Lock()
        # route-reliability cache (per stream / logical peer)
        self.route = {"acks": 0, "timeouts": 0, "last_rtt_ms": None,
                      "bytes_ok": 0}

    def settimeout(self, t: float):
        self._timeout = t

    def reliability(self) -> float:
        """Fraction of ACK waits that succeeded (0..1). Cold → 1.0."""
        n = self.route["acks"] + self.route["timeouts"]
        if n == 0:
            return 1.0
        return self.route["acks"] / n

    def sendall(self, data: bytes, priority: int | None = None):
        if self._closed:
            raise OSError("stream closed")
        if not data:
            return 0
        if priority is None:
            priority = _priority_for_payload(data)
        try:
            obj = json.loads(data)
            wire = _cbor.dumps(obj)
            self.link.stats["cbor_msgs"] = \
                self.link.stats.get("cbor_msgs", 0) + 1
        except (ValueError, UnicodeDecodeError):
            wire = _cbor.dumps(data)
        if self.link.psk is not None:
            wire = _encrypt(self.link.psk, wire)
            self.link.stats["enc_msgs"] = \
                self.link.stats.get("enc_msgs", 0) + 1
        with self._lock:
            mid = self._msg_id
            self._msg_id = (self._msg_id + 1) & 0xFFFF
            payloads = [wire[i:i + self.link.frame_cap - _HDR]
                        for i in range(0, len(wire),
                                       self.link.frame_cap - _HDR)]
            total = len(payloads)
            self._acked[mid] = set()
            for idx, payload in enumerate(payloads):
                frame = _pack(self.side, K_DATA, mid, idx, total, payload)
                tries = 0
                t0 = time.monotonic()
                while idx not in self._acked[mid]:
                    if tries > 0:
                        self.link.stats["retransmits"] += 1
                    self.link.send(self.side, frame, priority=priority)
                    tries += 1
                    if tries > 40:
                        self.route["timeouts"] += 1
                        raise OSError(
                            "radio link lost (no ack after 40 retries)")
                    ack = self.link.next_ack(self.side, 0.25)
                    if ack is not None and ack[0] == mid:
                        self._acked[mid].add(ack[1])
                self.route["acks"] += 1
                self.route["last_rtt_ms"] = round(
                    (time.monotonic() - t0) * 1000, 1)
            self.route["bytes_ok"] += len(data)
            return len(data)

    def recv(self, n: int) -> bytes:
        if self._closed:
            return b""
        deadline = time.monotonic() + self._timeout
        while True:
            res = self.link.next_message(self.side, 0.05)
            if res is None:
                if time.monotonic() >= deadline:
                    raise socket.timeout("radio timeout")
                continue
            mid, data = res
            if mid in self._seen:
                continue
            self._seen.add(mid)
            if data[:1] == bytes([_ENC_MAGIC]):
                if self.link.psk is None:
                    self.link.stats["dec_fail"] += 1
                    raise OSError("encrypted LoRa frame but no PSK configured")
                try:
                    data = _decrypt(self.link.psk, data)
                except Exception as exc:
                    self.link.stats["dec_fail"] += 1
                    raise OSError(f"LoRa AES-GCM decrypt failed: {exc}") from exc
            try:
                obj = _cbor.loads(data)
            except ValueError:
                return data
            if isinstance(obj, bytes):
                return obj
            return json.dumps(obj, separators=(",", ":")).encode() + b"\n"

    def close(self):
        self._closed = True
