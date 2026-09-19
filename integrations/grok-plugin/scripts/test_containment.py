#!/usr/bin/env python3
"""Offline smoke tests for Grok StatePod containment scripts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def run_hook(script: str, payload: dict) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(HERE / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ},
    )
    return p.returncode, p.stdout.strip()


def main() -> int:
    import contain_lib as cl

    # Isolate state under a temp HOME
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        os.environ["HOME"] = str(home)
        # reload paths by rewriting module constants via monkeypatch
        cl.HOME = home
        cl.STATE = home / ".statepod" / "omp.json"
        cl.OUT_DIR = home / ".statepod" / "grok" / "outbox"
        cl.LEDGER = home / ".statepod" / "grok" / "containment.jsonl"
        cl.GUIDED = home / ".statepod" / "grok" / "guided_sessions"

        # --- contain_text ---
        big = ("line\n" * 500) + ("x" * 5000)
        out = cl.contain_text(big, "call1", "read_file")
        assert out["digest"].startswith(cl.MARKER), out["digest"][:80]
        assert Path(out["path"]).exists()
        assert "omitted" in out["digest"]
        print("ok contain_text")

        cl.ledger_record(call_id="call1", tool="read_file", chars=len(big),
                         digest_chars=len(out["digest"]), cap=20000)
        assert cl.LEDGER.exists() and cl.LEDGER.read_text().strip()
        print("ok ledger")

        # --- guidance once ---
        g1 = cl.guidance_once("sessA", {"guidance": True, "capChars": 20000})
        g2 = cl.guidance_once("sessA", {"guidance": True, "capChars": 20000})
        assert g1 and g2 is None
        print("ok guidance_once")

        # enable for hook tests
        cl.write_cfg({**cl.DEFAULTS, "enabled": True, "guidance": False})

        # subprocess hooks see real HOME — set via env already
        # but contain_lib in subprocess re-reads Path.home() which uses HOME env ✓

        # --- pretool: read_file cap ---
        code, stdout = run_hook("pretool_rewrite.py", {
            "toolName": "read_file",
            "toolInput": {"target_file": "a.py"},
            "sessionId": "t1",
        })
        assert code == 0, stdout
        data = json.loads(stdout)
        assert data["hookSpecificOutput"]["updatedInput"]["limit"] == 200
        print("ok pretool read_file")

        # --- pretool: cat → head ---
        code, stdout = run_hook("pretool_rewrite.py", {
            "toolName": "run_terminal_command",
            "toolInput": {"command": "cat /tmp/x"},
            "sessionId": "t2",
        })
        assert code == 0
        data = json.loads(stdout)
        assert data["hookSpecificOutput"]["updatedInput"]["command"].startswith("head -n")
        print("ok pretool cat→head")

        # --- pretool: lean-ctx full → signatures ---
        code, stdout = run_hook("pretool_rewrite.py", {
            "toolName": "lean-ctx__ctx_read",
            "toolInput": {"path": "a.py", "mode": "full"},
            "sessionId": "t3",
        })
        assert code == 0
        data = json.loads(stdout)
        assert data["hookSpecificOutput"]["updatedInput"]["mode"] == "signatures"
        print("ok pretool ctx_read full→signatures")

        # --- pretool: grep head_limit ---
        code, stdout = run_hook("pretool_rewrite.py", {
            "toolName": "grep",
            "toolInput": {"pattern": "foo"},
            "sessionId": "t4",
        })
        assert code == 0
        data = json.loads(stdout)
        assert data["hookSpecificOutput"]["updatedInput"]["head_limit"] == 50
        print("ok pretool grep")

        # --- posttool archive ---
        code, stdout = run_hook("posttool_archive.py", {
            "toolName": "read_file",
            "toolUseId": "uid99",
            "toolResult": "Z" * 25000,
        })
        assert code == 0
        archived = list((home / ".statepod" / "grok" / "outbox").glob("uid99*.txt"))
        assert archived, "expected archive"
        print("ok posttool archive")

        # --- disabled → empty ---
        cl.write_cfg({**cl.DEFAULTS, "enabled": False})
        code, stdout = run_hook("pretool_rewrite.py", {
            "toolName": "read_file",
            "toolInput": {"target_file": "a.py"},
        })
        assert code == 0 and stdout in ("{}", "")
        print("ok disabled noop")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
