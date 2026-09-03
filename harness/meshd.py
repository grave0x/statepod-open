#!/usr/bin/env python3
"""meshd -- SwarmState mesh daemon (Phase 1: plaintext-LAN transport shim).

Runs ONE C-mesh peer (the shared stateless CRDT base at libmesh) and provides
the *transport* the C mesh deliberately leaves out: a TCP listener + outbound
peer connections that move JSON op dicts between `mesh_send_fn` and
`mesh_peer_apply_json`.

Phase 1 = GAPS #9 acceptance: two harnesses exchange >=2 registry STRAT
samples each way and converge on a shared state hash.  The convergence proof
is sha256(mesh_peer_state_json()) -- identical op set -> identical fold.

Transport is the swap point: replace the raw socket with a Tailcat tunnel
(Phase 2) or a LoRa adapter (Phase 3) and nothing else changes.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import time
import signal
import socket
import sys
import threading

from collections import deque

import meshbridge as mb




def _safe_rel_path(path: str) -> str | None:
    """Normalize a client-supplied file path to a safe RELATIVE path,
    or None if it would escape the share root (absolute or '..')."""
    if not isinstance(path, str) or not path.strip():
        return None
    rel = os.path.normpath(path.strip())
    if rel == "." or os.path.isabs(rel) or rel.startswith(".."):
        return None
    return rel


def registry_bridge(state):
    """Return an on_op callback that feeds accepted mesh ops into the
    kernel registry (collective memory -> local router).

    Mesh targets -> registry keys:
      reg/STRAT/<sig>/<strat>     -> STRAT:<sig>:<strat>     (strategy rates)
      reg/MODEL/<qsig>/<model>    -> MODEL:<qsig>:<model>    (routing rates)
    """
    def on_op(target, value):
        parts = str(target).split("/")
        ok = str(value) in ("1", "1.0", "True", "true")
        if len(parts) == 4 and parts[0] == "reg":
            # local-partition learning: reg/STRAT/<sig>/<strat>
            if parts[1] == "STRAT":
                state.feedback(f"STRAT:{parts[2]}:{parts[3]}", ok)
            elif parts[1] == "MODEL":
                state.feedback(f"MODEL:{parts[2]}:{parts[3]}", ok)
        elif len(parts) == 6 and parts[0] == "reg" and parts[1] == "POOL":
            # bridged learning arrives SOURCE-TAGGED (multi-homing):
            # reg/POOL/<src-pool>/STRAT/<sig>/<strat> -> it lands in the
            # POOL:<src>:* namespace, NEVER in the local partition.
            # Local routers read only untagged keys, so foreign learning
            # cannot pollute local success rates.
            _, _, src_pool, fam, sig, strat = parts
            if fam in ("STRAT", "MODEL"):
                state.feedback(f"POOL:{src_pool}:{fam}:{sig}:{strat}", ok)
    return on_op

class StdioStream:
    """Mimic the socket recv/sendall interface over stdin/stdout.

    Lets the SAME peer connection logic run over a Tailcat tunnel
    (tailcat's netcat-style stdio pipe) with zero changes: the transport
    is the swap point, and this is the Tailcat transport.
    """
    def __init__(self, stdin, stdout):
        self._in = stdin.buffer if hasattr(stdin, "buffer") else stdin
        self._out = stdout.buffer if hasattr(stdout, "buffer") else stdout
        self._buf = b""

    def settimeout(self, _t):
        pass

    def recv(self, n):
        if self._buf:
            data, self._buf = self._buf[:n], self._buf[n:]
            return data
        try:
            # read1 returns AVAILABLE bytes (one underlying read), like a
            # socket recv -- read(n) would block until n bytes or EOF.
            return self._in.read1(n)
        except OSError:
            return b""

    def sendall(self, data):
        self._out.write(data)
        self._out.flush()

    def close(self):
        pass


class MeshDaemon:
    def __init__(self, name, port, peers, allow=None, history_max=256,
                 log_stream=None, on_op=None, on_control=None,
                 pool_secret=None, pool_name=None, join_invite=None,
                 sim_load=0.0):
        self.name = name
        self.port = port
        # pool/QR-join (spec v1 5): hub verifies invites, mints allow
        # entries; a joiner dials the hub from the invite and receives
        # the member allowlist.
        self.pool_secret = pool_secret
        self.pool_name = pool_name or ("pool" if pool_secret else None)
        self.join_invite = join_invite
        self.pool_hub = None
        if pool_secret:
            from pool import PoolHub
            self.pool_hub = PoolHub(pool_secret, name, allow)
        self._join_done = threading.Event()
        # node_id -> socket (hello handshake; enables DIRECTED sends,
        # which the capability broker uses to route to a picked node)
        self._peers_by_id: dict[str, object] = {}
        # node_id -> {"models": [...], "load": float, "ts": epoch}
        self.capabilities: dict[str, dict] = {}
        self._inflight = 0      # provider requests being served
        self.sim_load = sim_load
        # on_control(msg): called for inbound req/resp control messages
        # (the inference-broker channel).  Providers register a handler
        # here; consumers use ask().
        self.on_control = on_control
        # id -> (event, result) for in-flight ask() requests
        self._waiters = {}
        # file serving (file_request/file_response): share root is None
        # until serve_files() is called -- default-deny by construction
        self.share_root = None
        self.share_patterns = ("*",)
        self.max_file_bytes = 65536
        # id -> (event, result) for in-flight request_file() calls
        self._file_waiters: dict[str, tuple] = {}
        # on_op(target, value): called for each NEWLY ACCEPTED remote op.
        # The harness bridges this into the kernel registry, closing the
        # collective-memory loop: peer learning feeds the local router.
        self.on_op = on_op
        # stdio transport reserves stdout for op frames; diagnostics go
        # to this stream (stderr in --stdio mode, stdout for TCP).
        self.log_stream = log_stream if log_stream is not None else sys.stdout
        self.peers = peers              # [(host, port), ...]
        self.allow = set(allow) if allow else None
        self.history = deque(maxlen=history_max)
        self.socks = []                 # live sockets (outbound + accepted)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        # set when the FIRST inbound data arrives on any connection:
        # signals the tunnel is up and catch-up is flowing (harnesses wait
        # on this before making their first registry-gated decision).
        self.ready = threading.Event()
        self.peer = mb.Peer(name, send=self._broadcast)

    # ── mesh hooks ───────────────────────────────────────────────────────
    def _broadcast(self, blob):
        """C mesh calls this on every LOCAL mutate (already applied)."""
        self._record(blob)
        self._send_all(blob, except_sock=None)

    def _send_all(self, blob, except_sock):
        frame = blob + b"\n"
        with self.lock:
            for s in list(self.socks):
                if s is except_sock:
                    continue
                try:
                    s.sendall(frame)
                except OSError:
                    pass

    def _record(self, blob):
        with self.lock:
            self.history.append(blob)

    def _apply(self, blob, src_sock):
        # trust boundary (v1): drop ops from non-allowlisted origins
        try:
            op = json.loads(blob)
            origin = (op.get("clock") or {}).get("origin", "")
        except (ValueError, AttributeError):
            return False
        if self.allow is not None and origin not in self.allow:
            return False
        accepted = self.peer.apply_json(blob)
        if accepted:
            self._record(blob)
            self._send_all(blob, except_sock=src_sock)   # relay
            if self.on_op is not None:
                try:
                    self.on_op(op.get("target", ""), op.get("value", ""))
                except Exception as e:   # never let a bridge error kill the
                    pass                 # mesh link (best-effort learning)
        return accepted

    # ── transport ────────────────────────────────────────────────────────
    # ── control messages (inference broker channel) ─────────────────────
    def send_control(self, msg: dict, sock=None):
        """Send a control message -- to one socket (directed) or to
        every peer (broadcast)."""
        frame = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        if sock is not None:
            with self.lock:
                try:
                    sock.sendall(frame)
                except OSError:
                    pass
            return
        with self.lock:
            for s in list(self.socks):
                try:
                    s.sendall(frame)
                except OSError:
                    pass

    def _handle_control(self, obj: dict, src_sock=None):
        mtype = obj.get("type")
        mid = obj.get("id")
        if mtype == "resp" and mid in self._waiters:
            event, holder = self._waiters[mid]
            holder["msg"] = obj
            event.set()
            return
        if mtype == "file_response" and mid in self._file_waiters:
            event, holder = self._file_waiters[mid]
            holder["msg"] = obj
            event.set()
            return
        if mtype == "file_request":
            try:
                self._serve_file_request(obj, src_sock)
            except Exception as exc:   # never kill the reader loop
                self.send_control({"type": "file_response",
                                   "id": obj.get("id"),
                                   "from": self.name, "ok": False,
                                   "reason": f"server error: {exc}"},
                                  sock=src_sock)
            return
        if mtype == "hello":
            n = obj.get("name")
            if n and src_sock is not None:
                self._peers_by_id[n] = src_sock
            return
        if mtype == "join":
            # pool hub: verify the invite, mint the member, reply with
            # the member allowlist, then push membership + history.
            if self.pool_hub is None:
                return
            res = self.pool_hub.join(obj.get("invite", ""),
                                     obj.get("name", ""))
            if res is None:
                self.send_control({"type": "join_denied"}, sock=src_sock)
                return
            if self.allow is not None:
                self.allow.add(obj.get("name", ""))
            # peers learn the new member BEFORE the joiner gets join_ok,
            # so its first ops are never dropped for want of membership
            for n in res["members"]:
                if n != obj.get("name"):
                    self.send_control({"type": "member_added",
                                       "name": obj.get("name")},
                                      sock=self._peers_by_id.get(n))
            self.send_control({"type": "join_ok", "pool": res["pool"],
                               "members": res["members"],
                               "node": self.name}, sock=src_sock)
            self._send_catchup(src_sock)   # joiner gets pool history
            return
        if mtype == "join_ok":
            members = obj.get("members") or []
            if members:
                self.allow = set(members)
            self.pool_name = obj.get("pool") or self.pool_name
            self._join_done.set()
            if src_sock is not None:
                self._send_catchup(src_sock)  # hub gets OUR history
            return
        if mtype == "member_added":
            n = obj.get("name")
            if n and self.allow is not None:
                self.allow.add(n)
            return
        if mtype == "capability":
            n = obj.get("name")
            if n:
                self.capabilities[n] = {
                    "models": obj.get("models") or [],
                    "load": float(obj.get("load", 0.0) or 0.0),
                    "ts": float(obj.get("ts", 0.0) or 0.0),
                }
            return
        if mtype == "lora_share":
            # compact batch of ops amortized into one radio message
            for op in obj.get("ops") or []:
                try:
                    self._apply(json.dumps(op), src_sock)
                except Exception:
                    pass
            return
        if self.on_control is not None:
            try:
                self.on_control(obj)
            except Exception:
                pass

    def load(self) -> float:
        """Current provider load: simulated knob wins, else in-flight
        requests (single-slot models -> 0 or 1)."""
        if self.sim_load > 0.0:
            return min(self.sim_load, 1.0)
        return min(self._inflight, 1.0)

    def pick_provider(self, prefer_models: list[str] | None,
                      now: float | None = None) -> str | None:
        """Capability broker (spec v2 6.1): score known providers by
        model match, load, and freshness; return the best node id."""
        if not self.capabilities or not prefer_models:
            return None
        now = now or time.time()
        best, best_score = None, float("-inf")
        for node, cap in self.capabilities.items():
            stale = 1.0 if now - cap["ts"] > 15.0 else 0.0
            match = 2.0 if any(p in cap["models"] for p in prefer_models) else 0.0
            score = match - 3.0 * cap["load"] - 2.0 * stale
            if score > best_score:
                best, best_score = node, score
        # all providers loaded/stale? broadcast and let whoever is
        # actually free answer first
        if best_score < 0.0:
            return None
        return best

    def _ask_to(self, rid: str, query: str, summary: str, node: str) -> bool:
        sock = self._peers_by_id.get(node)
        if sock is None:
            return False
        self.send_control({"type": "req", "id": rid,
                           "query": query, "summary": summary or ""},
                          sock=sock)
        return True

    def ask(self, query: str, summary: str | None = None,
            timeout: float = 20.0, prefer_models: list[str] | None = None,
            to: str | None = None) -> dict:
        """Consumer: inference_request -> plan.

        Routing (the broker):
          to=<node>        directed to that provider
          prefer_models    pick the best provider by capability score
                           (model match, load, freshness), directed ask
          otherwise        broadcast (any provider answers)
        Directed attempts fall back to a broadcast ask on timeout, and
        the orchestrator falls back to local inference if that fails."""
        import uuid
        rid = str(uuid.uuid4())[:12]
        event = threading.Event()
        holder: dict = {}
        self._waiters[rid] = (event, holder)

        def send():
            if to is not None and self._ask_to(rid, query, summary, to):
                return True
            if prefer_models:
                node = self.pick_provider(prefer_models)
                if node is not None and self._ask_to(rid, query, summary, node):
                    return True
            self.send_control({"type": "req", "id": rid,
                               "query": query, "summary": summary or ""})
            return True

        try:
            send()
            if not event.wait(timeout):
                if to is not None or prefer_models:
                    # no answer from the picked node: ask everyone
                    self.send_control({"type": "req", "id": rid,
                                       "query": query,
                                       "summary": summary or ""})
                if not event.wait(timeout):
                    raise TimeoutError(
                        f"no provider answered inference request {rid} "
                        f"within {timeout:.0f}s")
            msg = holder.get("msg") or {}
            if not msg.get("ok"):
                raise RuntimeError(
                    f"provider rejected request {rid}: {msg.get('error')}")
            return msg.get("plan") or {}
        finally:
            self._waiters.pop(rid, None)

    def serve_model(self, model: str, backend: str = "ollama"):
        """Provider: answer inference requests with a local model."""
        import sys as _sys
        from pathlib import Path as _P
        # standalone meshd needs the repo's harness/ and py/ on the path
        # (planner -> swarmstate C binding) before importing planner.
        _root = _P(__file__).resolve().parent.parent
        for _d in (_root / "harness", _root / "py"):
            if str(_d) not in _sys.path:
                _sys.path.insert(0, str(_d))
        from planner import make_plan, normalize_plan

        def on_req(obj: dict):
            if obj.get("type") != "req":
                return
            rid = obj.get("id")
            self._inflight += 1
            try:
                plan = normalize_plan(make_plan(
                    obj.get("query", ""), obj.get("summary") or None,
                    backend=backend, model=model))
                resp = {"type": "resp", "id": rid, "ok": True,
                        "plan": plan, "model": model,
                        "load": self.load()}
            except Exception as exc:
                resp = {"type": "resp", "id": rid, "ok": False,
                        "error": str(exc), "model": model,
                        "load": self.load()}
            finally:
                self._inflight -= 1
            self.send_control(resp)
            print(f"[mesh-provider] answered {rid} ok={resp['ok']} "
                  f"model={model} load={resp.get('load')}",
                  file=_sys.stderr, flush=True)

        self.on_control = on_req

    # ── file_request / file_response (spec v1 App. B) ──────────────
    def serve_files(self, share_root: str, allow_patterns=("*",),
                    max_bytes: int = 65536):
        """Provider: serve files from a share root in answer to
        file_request.  Default-deny by construction:
          - only RELATIVE paths inside the root (no '..', no absolute)
          - only paths matching allow_patterns (glob)
          - only files up to max_bytes (control channel, not transfer)"""
        self.share_root = os.path.abspath(share_root)
        self.share_patterns = tuple(allow_patterns)
        self.max_file_bytes = int(max_bytes)

    def request_file(self, path: str, to: str | None = None,
                     timeout: float = 10.0) -> dict:
        """Consumer: file_request -> file_response (directed).

        to=<node> asks a specific known peer.  Denials and timeouts come
        back as {ok: False, reason: ...} or raise TimeoutError, so the
        caller can fall back to local state -- never assume the file
        exists on the wire."""
        import uuid
        rid = str(uuid.uuid4())[:12]
        event = threading.Event()
        holder: dict = {}
        self._file_waiters[rid] = (event, holder)
        try:
            if to is not None:
                sock = self._peers_by_id.get(to)
                if sock is None:
                    raise RuntimeError(
                        f"no known peer named {to!r} to ask for a file")
            else:
                if not self._peers_by_id:
                    raise RuntimeError(
                        "no peers connected; nothing to ask for a file")
                to = sorted(self._peers_by_id)[0]
                sock = self._peers_by_id[to]
            self.send_control({"type": "file_request", "id": rid,
                               "path": path, "to": to}, sock=sock)
            if not event.wait(timeout):
                raise TimeoutError(
                    f"no file_response for {path!r} within {timeout:.0f}s")
            return holder.get("msg") or {}
        finally:
            self._file_waiters.pop(rid, None)

    def _serve_file_request(self, obj: dict, src_sock):
        """Provider side: answer (or deny, always with a reason)."""
        mid = obj.get("id")

        def reply(**kw):
            self.send_control({"type": "file_response", "id": mid,
                               "from": self.name, **kw}, sock=src_sock)

        if self.share_root is None:
            return reply(ok=False, reason="this node is not serving files")
        rel = _safe_rel_path(obj.get("path", ""))
        if rel is None:
            return reply(ok=False, reason="path escapes the share root")
        cand = os.path.join(self.share_root, rel)
        # allowlist first: an unallowlisted path is refused regardless
        # of existence, so the policy never doubles as a file oracle
        if not any(fnmatch.fnmatch(rel, pat)
                   for pat in self.share_patterns):
            return reply(ok=False, reason="path not in serve patterns")
        if not os.path.isfile(cand):
            return reply(ok=False, reason=f"no such file: {rel}")
        size = os.path.getsize(cand)
        if size > self.max_file_bytes:
            return reply(ok=False, reason=f"file too large "
                                          f"({size} > {self.max_file_bytes})")
        try:
            with open(cand, "r", encoding="utf-8",
                      errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            return reply(ok=False, reason=f"read failed: {exc}")
        return reply(ok=True, name=rel, size=size, content=content)

    def _send_catchup(self, sock):
        """Ship our whole op history to a peer (idempotent: the mesh
        dedups by Lamport clock, so re-sending is always safe)."""
        with self.lock:
            snapshot = list(self.history)
        for blob in snapshot:
            try:
                sock.sendall(blob + b"\n")
            except OSError:
                return

    def _handle_conn(self, sock, addr):
        sock.settimeout(0.5)
        with self.lock:
            self.socks.append(sock)
        # hello handshake: announce our node id so the peer can map
        # id -> socket (directed sends / capability broker routing).
        self.send_control({"type": "hello", "name": self.name}, sock=sock)
        # joiner onboarding: present the signed invite to the hub.
        if self.join_invite:
            self.send_control({"type": "join", "invite": self.join_invite,
                               "name": self.name}, sock=sock)
        # catch-up: snapshot under the lock, then send OUTSIDE it (a slow
        # peer must not block local mutation or the reader loop).
        self._send_catchup(sock)
        buf = b""
        first = True
        while not self.stop.is_set():
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            if first:
                first = False
                # The peer is now actually on the line.  Lazy transports
                # (Tailcat stdio) come up AFTER our startup catch-up fired,
                # and drop pre-connection writes -- so re-send our history
                # once the tunnel is confirmed live.  Dedup keeps it safe.
                self._send_catchup(sock)
                self.ready.set()
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    self._apply(line, sock)
                    continue
                if isinstance(obj, dict) and "type" in obj:
                    self._handle_control(obj, src_sock=sock)
                else:
                    self._apply(line, sock)
        with self.lock:
            if sock in self.socks:
                self.socks.remove(sock)
        try:
            sock.close()
        except OSError:
            pass

    def _listen(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(8)
        srv.settimeout(0.5)
        while not self.stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn, addr),
                             daemon=True).start()
        try:
            srv.close()
        except OSError:
            pass

    def _connect_loop(self, host, port):
        while not self.stop.is_set():
            try:
                s = socket.create_connection((host, port), timeout=3)
                self._handle_conn(s, (host, port))
                return
            except OSError:
                self.stop.wait(2)

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self, publish_ops=()):
        """Non-blocking: start listener + outbound peers + stdin feed.
        In-process harnesses call this and keep running their own loop."""
        threading.Thread(target=self._listen, daemon=True).start()
        for host, port in self.peers:
            threading.Thread(target=self._connect_loop, args=(host, port),
                             daemon=True).start()
        # startup publishes (registry STRAT samples, etc.)
        for target, value in publish_ops:
            self.peer.mutate(mb.MESH_OP_APP, target, value)
        # stdin publish: target<TAB>value per line (interactive harness feed)
        if not sys.stdin.isatty():
            threading.Thread(target=self._stdin_feed, daemon=True).start()

    def publish(self, target: str, value: str):
        """Append an op to the shared mesh (local apply + broadcast).
        Registry STRAT/MODEL learning feeds this as it happens."""
        assert len(target) < 96, f"target too long ({len(target)}): {target}"
        self.peer.mutate(mb.MESH_OP_APP, target, value)

    def publish_many(self, ops: list[tuple[str, str]]):
        """Append several ops and broadcast them as ONE lora_share batch
        (compact radio adaptation: one message, not N).

        mutate() computes the AUTHORITATIVE Lamport clock; we suppress
        the per-op transport callback so the batch is the only send
        (hand-rolled clocks would break convergence -- never do that)."""
        if not ops:
            return
        old_send, self.peer.send = self.peer.send, None
        blobs = []
        try:
            for target, value in ops:
                assert len(target) < 96
                self.peer.mutate(mb.MESH_OP_APP, target, value)
                blobs.append(json.loads(mb.op_json(
                    self.name, "append", target, value,
                    self.peer.latest()["counter"])))
        finally:
            self.peer.send = old_send
        self.send_control({"type": "lora_share", "ops": blobs})

    def announce_capabilities(self, models: list[str], interval: float = 5.0):
        """Periodically broadcast a capability_announce (models, load,
        freshness) so the broker can score this provider."""
        def _loop():
            while not self.stop.is_set():
                self.send_control({"type": "capability", "name": self.name,
                                   "models": models,
                                   "load": self.load(),
                                   "ts": time.time()})
                self.stop.wait(interval)
        threading.Thread(target=_loop, daemon=True).start()

    def run(self, publish_ops, hash_interval=1.0):
        self.start(publish_ops)
        while not self.stop.is_set():
            self._print_hash()
            self.stop.wait(hash_interval)

    def _stdin_feed(self):
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                target, value = line.split("\t", 1)
            else:
                target, value = line.split(None, 1)
            self.peer.mutate(mb.MESH_OP_APP, target, value)

    def _print_hash(self):
        st = self.peer.state_json()
        h = hashlib.sha256(st.encode()).hexdigest()
        stats = self.peer.stats()
        print(f"HASH {h}  tail={stats['tail_ops']} rss_kb={stats['rss_kb']}",
              file=self.log_stream, flush=True)

    def shutdown(self):
        self.stop.set()
        self._print_hash()
        self.peer.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="peer id (clock origin)")
    ap.add_argument("--port", type=int, default=0,
                    help="listen port (TCP mode; unused in --stdio mode)")
    ap.add_argument("--peer", action="append", default=[],
                    metavar="HOST:PORT", help="outbound peer (repeatable)")
    ap.add_argument("--allow", default="",
                    help="comma-separated peer ids allowed to inject ops")
    ap.add_argument("--publish", action="append", default=[],
                    metavar="TARGET\\tVALUE", help="publish an op at startup")
    ap.add_argument("--hash-interval", type=float, default=1.0)
    ap.add_argument("--stdio", action="store_true",
                    help="use stdin/stdout as the peer link (Tailcat tunnel "
                         "transport) instead of TCP listen/connect")
    ap.add_argument("--serve-model", default=None,
                    help="inference-broker provider: answer req messages "
                         "with this local model")
    ap.add_argument("--serve-backend", default="ollama",
                    help="provider planning backend (default ollama)")
    ap.add_argument("--announce-models", default=None,
                    help="comma-separated models to announce (capability "
                         "broker input; announce every 5s)")
    ap.add_argument("--sim-load", type=float, default=0.0,
                    help="simulated provider load 0..1 (demo knob)")
    ap.add_argument("--pool-secret", default=None,
                    help="enable pool hub: verify join invites with this "
                         "secret (spec v1 section 5)")
    ap.add_argument("--pool-name", default=None,
                    help="pool display name (default: 'pool')")
    ap.add_argument("--join", default=None,
                    help="join a pool: signed invite string (QR payload); "
                         "node auto-onboards and receives the member list")
    ap.add_argument("--lora-publish", action="append", default=[],
                    metavar="TARGET\tVALUE",
                    help="publish ops as ONE lora_share batch at startup "
                         "(narrowband radio adaptation)")
    ap.add_argument("--serve-files", default=None, metavar="ROOT",
                    help="serve files from this share root in answer to "
                         "file_request (default-deny: relative paths, "
                         "pattern allowlist, size cap)")
    ap.add_argument("--file-patterns", default="*",
                    help="comma-separated globs allowed from the share "
                         "root (quote it: '*.py,conf/*'; default: *)")
    ap.add_argument("--max-file-bytes", type=int, default=65536,
                    help="largest served file (control channel, not a "
                         "file-transfer pipe; default 65536)")
    args = ap.parse_args(argv)

    peers = []
    for spec in args.peer:
        host, _, port = spec.rpartition(":")
        peers.append((host or "127.0.0.1", int(port)))
    allow = [x for x in args.allow.split(",") if x] or None
    pubs = [tuple(p.split("\t", 1)) if "\t" in p else
            tuple(p.split(None, 1)) for p in args.publish]

    def wire_files(d):
        """--serve-files/--file-patterns/--max-file-bytes."""
        if args.serve_files:
            d.serve_files(args.serve_files,
                          tuple(x.strip() for x in
                                args.file_patterns.split(",") if x.strip()),
                          args.max_file_bytes)

    if args.stdio:
        # Tailcat transport: stdin/stdout is ONE peer link.  Run the same
        # connection loop over a stream that mimics a socket.
        d = MeshDaemon(args.name, 0, [], allow, log_stream=sys.stderr,
                       pool_secret=args.pool_secret,
                       pool_name=args.pool_name,
                       join_invite=args.join, sim_load=args.sim_load)
        wire_files(d)
        signal.signal(signal.SIGINT, lambda *a: d.shutdown())
        signal.signal(signal.SIGTERM, lambda *a: d.shutdown())
        if args.announce_models:
            d.announce_capabilities(
                [m for m in args.announce_models.split(",") if m])
        for target, value in pubs:
            d.publish(target, value)
        if args.lora_publish:
            d.publish_many([tuple(p.split("\t", 1))
                            if "\t" in p else tuple(p.split(None, 1))
                            for p in args.lora_publish])
        stream = StdioStream(sys.stdin, sys.stdout)
        threading.Thread(target=d._handle_conn, args=(stream, "stdio"),
                         daemon=True).start()
        while not d.stop.is_set():
            d._print_hash()
            d.stop.wait(args.hash_interval)
        return

    d = MeshDaemon(args.name, args.port, peers, allow,
                   pool_secret=args.pool_secret,
                   pool_name=args.pool_name,
                   join_invite=args.join, sim_load=args.sim_load)
    wire_files(d)
    if args.serve_model:
        d.serve_model(args.serve_model, args.serve_backend)
    if args.announce_models:
        d.announce_capabilities(
            [m for m in args.announce_models.split(",") if m])
    signal.signal(signal.SIGINT, lambda *a: d.shutdown())
    signal.signal(signal.SIGTERM, lambda *a: d.shutdown())
    d.run(pubs, args.hash_interval)


if __name__ == "__main__":
    main()
