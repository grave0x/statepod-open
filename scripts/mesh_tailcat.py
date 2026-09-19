#!/usr/bin/env python3
"""mesh_tailcat.py -- run a StatePod mesh node over a Tailcat tunnel.

Tailcat (github.com/tailscale/tailcat) gives point-to-point WireGuard
tunnels with no control plane.  Its raw `tailcat` stdio mode is ONE-way
(client->server); full-duplex comes from `tailcat --serve=<port>`, which
forwards a local TCP port through the tunnel.

So the node roles are asymmetric (this matches how the transport is a
*swap*: the server keeps its normal TCP meshd, the client bridges stdio):

  # Node A (server): normal TCP meshd on :7001 + tailcat exposing it.
  #   The token is printed to stderr.
  python3 scripts/mesh_tailcat.py server --name alice --port 7001 \\
      --publish $'reg/STRAT/a:one:DELTA\\t1'

  # Node B (client): dial the token, bridge stdio to a mesh peer.
  python3 scripts/mesh_tailcat.py client --name bob --token <TOKEN> \\
      --port 7001 --publish $'reg/STRAT/b:two:DELTA\\t1'

Both nodes fold the same op set and print the same sha256(state_json)
to stderr.  The token is the entire invitation (spec v2 §4.1).
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

from meshd import MeshDaemon, StdioStream  # noqa: E402

TAILCAT = os.environ.get("TAILCAT_BIN") or shutil.which("tailcat")


def _tee(stream):
    for line in stream:
        sys.stderr.write(line.decode(errors="replace"))
        sys.stderr.flush()


def _hash_loop(d):
    signal.signal(signal.SIGINT, lambda *a: d.shutdown())
    signal.signal(signal.SIGTERM, lambda *a: d.shutdown())
    while not d.stop.is_set():
        d._print_hash()
        d.stop.wait(1.0)


def run_server(name, port, publish_ops, allow):
    # tailcat --serve=<port> forwards the tunnel to localhost:<port>;
    # the meshd keeps its normal TCP listener on that port.
    tc = subprocess.Popen([TAILCAT, f"--serve={port}"],
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    threading.Thread(target=_tee, args=(tc.stderr,), daemon=True).start()

    d = MeshDaemon(name, port, [], [x for x in allow.split(",") if x] or None,
                   log_stream=sys.stderr)
    d.start(publish_ops)          # TCP listener + startup publishes
    try:
        _hash_loop(d)
    finally:
        tc.terminate()
        d.shutdown()


def run_client(name, token, port, publish_ops, allow):
    # tailcat <token> <port> gives a full-duplex stdio stream to the
    # server's meshd port.
    tc = subprocess.Popen([TAILCAT, token, str(port)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    threading.Thread(target=_tee, args=(tc.stderr,), daemon=True).start()

    stream = StdioStream(tc.stdout, tc.stdin)
    d = MeshDaemon(name, 0, [], [x for x in allow.split(",") if x] or None,
                   log_stream=sys.stderr)
    for target, value in publish_ops:
        d.publish(target, value)
    threading.Thread(target=d._handle_conn, args=(stream, "client"),
                     daemon=True).start()
    try:
        _hash_loop(d)
    finally:
        tc.terminate()
        d.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["server", "client"])
    ap.add_argument("--name", required=True, help="peer id")
    ap.add_argument("--port", type=int, default=7001,
                    help="server meshd TCP port (both sides must agree)")
    ap.add_argument("--token", default=None,
                    help="connection token (client mode)")
    ap.add_argument("--publish", action="append", default=[],
                    metavar="TARGET\\tVALUE")
    ap.add_argument("--allow", default="")
    args = ap.parse_args()

    if not TAILCAT:
        sys.exit("tailcat not found: go install "
                 "github.com/tailscale/tailcat/cmd/tailcat@latest")
    if args.mode == "client" and not args.token:
        sys.exit("client mode requires --token")
    pubs = [tuple(p.split("\t", 1)) if "\t" in p else tuple(p.split(None, 1))
            for p in args.publish]

    if args.mode == "server":
        run_server(args.name, args.port, pubs, args.allow)
    else:
        run_client(args.name, args.token, args.port, pubs, args.allow)


if __name__ == "__main__":
    main()
