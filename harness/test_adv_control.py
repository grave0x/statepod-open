#!/usr/bin/env python3
"""Self-check: adv-review C2/C3/H2/H3/C4 control-plane hardening."""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

# harness/ is the cwd package root when run from here
sys.path.insert(0, os.path.dirname(__file__))


def test_join_ok_unsolicited_ignored():
    from meshd import MeshDaemon
    a = MeshDaemon("victim", 0, [], allow={"victim", "friend"})
    before = set(a.allow)
    # spoof join_ok on a fake sock object — no prior join
    fake = object()
    a._handle_control({"type": "join_ok", "members": ["attacker"], "pool": "x"},
                      src_sock=fake)
    assert a.allow == before, a.allow


def test_member_added_requires_allowlisted_sender():
    from meshd import MeshDaemon
    a = MeshDaemon("victim", 0, [], allow={"victim", "hub"})
    sock = object()
    a._peers_by_id["hub"] = sock
    a._handle_control({"type": "member_added", "name": "newbie"}, src_sock=sock)
    assert "newbie" in a.allow
    evil = object()
    a._peers_by_id["attacker"] = evil
    a._handle_control({"type": "member_added", "name": "pwned"}, src_sock=evil)
    assert "pwned" not in a.allow


def test_empty_allow_denies_remote_ops():
    from meshd import MeshDaemon
    d = MeshDaemon("solo", 0, [], allow=[])  # empty set, not open
    assert d.allow == set()
    blob = b'{"clock":{"origin":"evil","n":1},"type":"app","target":"reg/STRAT/x/y","value":"1"}'
    assert d._apply(blob, None) is False


def test_lora_require_psk():
    from lora import LoRaLink
    try:
        LoRaLink(require_psk=True, psk=None)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    # 32-byte key ok
    LoRaLink(require_psk=True, psk=b"0" * 32).shutdown()


def test_gov_ui_refuses_no_secret():
    import subprocess
    r = subprocess.run(
        [sys.executable, "gov_ui.py", "--port", "0"],
        cwd=os.path.dirname(__file__),
        capture_output=True, text=True, env={**os.environ, "SS_GOV_SECRET": ""},
        timeout=5,
    )
    # --port 0 may still bind; we care about exit before serve if no secret
    assert r.returncode != 0
    assert "refuse" in (r.stderr + r.stdout).lower() or r.returncode == 1


if __name__ == "__main__":
    test_join_ok_unsolicited_ignored()
    test_member_added_requires_allowlisted_sender()
    test_empty_allow_denies_remote_ops()
    test_lora_require_psk()
    test_gov_ui_refuses_no_secret()
    print("OK test_adv_control")
