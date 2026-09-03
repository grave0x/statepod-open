"""Pools + QR-join (spec v1 section 5).

A pool is a set of devices that trust each other fully.  Membership is
explicit: an admin issues a signed, short-lived INVITE; a new node
"scans" it (or pastes the string) and presses one button.  The hub
verifies the invite, mints the member's allowlist entry, and pushes the
membership to every existing peer.

  Default posture: no pool may reach another without both sides
  accepting (the --allow node-id allowlist is the mechanical gate; the
  invite is the onboarding ceremony).

Invite format (compact, QR-friendly):
  base64url( payload_bytes + hmac(secret, payload_bytes) )
  payload = json{"v":1,"pool":..., "hub":"host:port", "nonce":...,
                 "exp":epoch}
  signature = fixed LAST 32 bytes (never a dot-delimited framing --
  raw HMAC bytes can contain any byte, see governance.issue_token).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_SIG_LEN = hashlib.sha256().digest_size


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_invite(secret: str, pool: str, hub: str, ttl: int = 3600,
                 nonce: str | None = None) -> str:
    """Mint a signed, expiring pool invite.  `hub` is host:port of the
    pool hub the joiner must dial.  Returns the QR payload string."""
    if not secret:
        raise ValueError("pool: no pool secret configured")
    payload = json.dumps({
        "v": 1, "pool": pool, "hub": hub,
        "nonce": nonce or os.urandom(8).hex(),
        "exp": int(time.time()) + ttl,
    }, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return _b64e(payload + sig)


def verify_invite(invite: str, secret: str) -> dict | None:
    """Return the invite payload if authentic + unexpired, else None."""
    if not invite or not secret:
        return None
    try:
        raw = _b64d(invite)
        if len(raw) <= _SIG_LEN:
            return None
        sig, payload = raw[-_SIG_LEN:], raw[:-_SIG_LEN]
        expect = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(payload)
        if data.get("v") != 1 or time.time() > data.get("exp", 0):
            return None
        return data
    except Exception:
        return None


def render_qr(text: str, out: str | None = None, terminal: bool = True):
    """Render the invite as a QR code (qrencode binary) to a PNG file
    and/or the terminal.  Output is captured and printed AFTER any
    earlier print() calls (mixed buffering would otherwise interleave
    the QR art ahead of the invite in a pipe)."""
    qr = shutil.which("qrencode")
    if qr is None:
        print("[pool] qrencode not found; invite (paste it):")
        print(text)
        return False
    if out:
        subprocess.run([qr, "-o", out, text], check=True)
        print(f"[pool] QR written to {out}")
    if terminal:
        art = subprocess.run([qr, "-t", "ANSIUTF8", text],
                             capture_output=True).stdout
        sys.stdout.write(art.decode(errors="replace"))
    return True


class PoolHub:
    """Server-side membership state: verify invites, track members."""

    MAX_USED = 8192

    def __init__(self, secret: str, name: str, allow: set[str] | None = None):
        self.secret = secret
        self.name = name
        self.members: dict[str, str] = {}  # node_id -> name (== id here)
        # nonce -> (joiner_name, exp): invites are single-use PER IDENTITY.
        # A replay from the SAME node id is a reconnect (accepted); a replay
        # from a DIFFERENT id is a stolen/shared invite (denied).
        self._used: dict[str, tuple[str, int]] = {}
        if allow:
            for a in allow:
                self.members.setdefault(a, a)

    def join(self, invite: str, joiner_name: str) -> dict | None:
        data = verify_invite(invite, self.secret)
        if data is None:
            return None
        if not joiner_name or len(joiner_name) > 31:
            return None
        nonce = str(data.get("nonce") or "")
        exp = int(data.get("exp") or 0)
        # prune expired nonces so the map stays bounded
        if len(self._used) > self.MAX_USED:
            now = time.time()
            self._used = {k: v for k, v in self._used.items() if v[1] > now}
        prev = self._used.get(nonce)
        if prev is not None and prev[0] != joiner_name:
            return None  # invite already consumed by a different identity
        self._used[nonce] = (joiner_name, exp)
        self.members[joiner_name] = joiner_name
        return {"pool": data["pool"], "members": sorted(self.members)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("issue", help="mint an invite (+ optional QR)")
    i.add_argument("--secret", required=True)
    i.add_argument("--pool", required=True)
    i.add_argument("--hub", required=True, help="host:port of the pool hub")
    i.add_argument("--ttl", type=int, default=3600)
    i.add_argument("--qr", action="store_true", help="render as QR code")
    i.add_argument("--qr-out", default=None, help="PNG path for the QR")

    v = sub.add_parser("verify", help="check an invite")
    v.add_argument("--secret", required=True)
    v.add_argument("--invite", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "issue":
        invite = issue_invite(args.secret, args.pool, args.hub, args.ttl)
        print(invite, flush=True)   # flush before any QR art
        if args.qr:
            render_qr(invite, args.qr_out)
    else:
        data = verify_invite(args.invite, args.secret)
        print(json.dumps(data, indent=2) if data else "INVALID or EXPIRED")


if __name__ == "__main__":
    main()
