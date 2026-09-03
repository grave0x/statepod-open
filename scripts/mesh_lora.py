#!/usr/bin/env python3
"""mesh_lora.py -- run SwarmState mesh nodes over a simulated LoRa radio.

The radio is a swap shim (same seam as Tailcat): nodes talk through
LoRaStream objects that mimic a socket, so meshd._handle_conn runs
unchanged.  The link simulates narrowband reality: frame caps (~64 B
payloads), half-duplex serial airtime, configurable loss with a
stop-and-wait ARQ, and lora_share batching (one radio message carries
many ops).

  # Node A publishes a registry batch over the radio
  python3 scripts/mesh_lora.py --name field-a --role a \\
      --publish $'reg/STRAT/WRITE:DELTA\\t1' --publish $'reg/MODEL:q:x:7b\\t1'

Both nodes fold the same op set and print the same sha256(state_json)
to stderr; radio stats (frames, drops, retransmits, airtime) print at
the end.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

from lora import LoRaLink  # noqa: E402
from meshd import MeshDaemon  # noqa: E402


def run_node(name, role, link, publish_ops, loss, airtime, seed):
    link.log = lambda *a: None  # quiet; stats printed at the end
    stream = link.endpoint(role.upper())
    d = MeshDaemon(name, 0, [], None, log_stream=sys.stderr)
    signal.signal(signal.SIGINT, lambda *a: d.shutdown())
    signal.signal(signal.SIGTERM, lambda *a: d.shutdown())
    threading.Thread(target=d._handle_conn, args=(stream, "lora"),
                     daemon=True).start()
    time.sleep(1.0)               # radio link up + catch-up flowing
    if publish_ops:
        d.publish_many(publish_ops)   # ONE lora_share batch, not N lines
    try:
        while not d.stop.is_set():
            d._print_hash()
            d.stop.wait(1.0)
    finally:
        d.shutdown()
        return link


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="peer id")
    ap.add_argument("--role", choices=["a", "b"], default="a",
                    help="which radio endpoint this node uses")
    ap.add_argument("--peer-name", default=None,
                    help="other node's peer id (printed in stats)")
    ap.add_argument("--publish", action="append", default=[],
                    metavar="TARGET\\tVALUE")
    ap.add_argument("--loss", type=float, default=0.1,
                    help="radio frame loss probability (ARQ retransmits)")
    ap.add_argument("--airtime", type=float, default=0.02,
                    help="seconds per radio frame (half-duplex)")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    pubs = [tuple(p.split("\t", 1)) if "\t" in p else tuple(p.split(None, 1))
            for p in args.publish]
    # the two nodes are two processes sharing one radio: run them both
    # here (A publisher, B receiver) so the link object is shared.
    import subprocess
    # Simplest honest wiring for the smoke test: fork node B in-thread.
    # For two-machine operation, swap LoRaLink for real radio hardware.
    link = LoRaLink(frame_cap=96, p_loss=args.loss,
                    airtime=args.airtime, seed=args.seed)
    got = {}

    def run_b():
        b = MeshDaemon("node-b", 0, [], None, log_stream=sys.stderr)
        stream = link.endpoint("B")
        threading.Thread(target=b._handle_conn, args=(stream, "lora"),
                         daemon=True).start()
        _orig_print = b._print_hash

        import hashlib as _hl
        def _print_hash():
            _orig_print()
            st = b.peer.state_json()
            print("B-HASH", _hl.sha256(st.encode()).hexdigest(),
                  file=sys.stderr, flush=True)
        b._print_hash = _print_hash
        while not b.stop.is_set():
            _print_hash()
            b.stop.wait(1.0)
        b.shutdown()

    tb = threading.Thread(target=run_b, daemon=True)
    tb.start()
    run_node(args.name, args.role, link, pubs, args.loss,
             args.airtime, args.seed)
    link.shutdown()
    s = link.stats
    kbps = (s["payload_bytes"] * 8 / 1000) / max(s["airtime_s"], 1e-9)
    print(f"[lora] stats: {s['data_frames']} data frames, "
          f"{s['ack_frames']} acks, {s['dropped']} dropped, "
          f"{s['retransmits']} retransmits, {s['payload_bytes']}B in "
          f"{s['airtime_s']:.1f}s airtime (~{kbps:.1f} kbps effective)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
