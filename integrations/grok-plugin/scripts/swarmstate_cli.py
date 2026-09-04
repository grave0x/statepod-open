#!/usr/bin/env python3
"""CLI for /swarmstate on|off|status|stats|full|cap — shares ~/.swarmstate/omp.json."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contain_lib import (  # noqa: E402
    LEDGER,
    OUT_DIR,
    STATE,
    fmt_size,
    read_cfg,
    write_cfg,
)

HOME = Path.home()
REPO = Path(os.environ.get(
    "SWARMSTATE_REPO",
    str(HOME / "Projects/internal.source/02-tools/swarmstate"),
))


def kernel_status() -> str:
    py = REPO / "py"
    lib = REPO / "libswarmstate.so"
    code = (
        "import sys,os;"
        f"sys.path.insert(0,{str(py)!r});"
        f"os.environ.setdefault('SWARMSTATE_LIB',{str(lib)!r});"
        "from swarmstate import SwarmState;"
        "s=SwarmState(os.path.expanduser('~/.swarmstate'));"
        "si=s.sysinfo();t=s.resource_tag();"
        "print(f\"{t} | load {si['load1']:.1f}/{si['load5']:.1f}/{si['load15']:.1f} "
        "| mem {si['mem_avail_kb']//1024}/{si['mem_total_kb']//1024}M "
        "| up {si['uptime_sec']}s\")"
    )
    try:
        (HOME / ".swarmstate").mkdir(parents=True, exist_ok=True)
        return subprocess.check_output(
            ["python3", "-c", code], text=True, timeout=3,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def main(argv: list[str]) -> int:
    sub = (argv[1] if len(argv) > 1 else "status").lower()
    cfg = read_cfg()
    if sub == "on":
        cfg["enabled"] = True
        write_cfg(cfg)
        print(
            f"SwarmState: ON — cap {cfg['capChars']:,} chars · "
            f"readLimit {cfg['readLimit']} · grepMax {cfg['grepMax']}"
        )
        print("Grok path: PreToolUse hardens dumps; PostToolUse archives oversized "
              "results (cannot rewrite tool results — see PARITY.md).")
    elif sub == "off":
        cfg["enabled"] = False
        write_cfg(cfg)
        print("SwarmState: OFF")
    elif sub == "cap":
        if len(argv) < 3:
            print(f"capChars={cfg['capChars']}")
            return 0
        try:
            n = int(argv[2])
        except ValueError:
            print("usage: swarmstate_cli.py cap <chars>")
            return 2
        if n < 1000:
            print("cap must be >= 1000")
            return 2
        cfg["capChars"] = n
        write_cfg(cfg)
        print(f"capChars={n}")
    elif sub == "stats":
        runs = saved = chars = 0
        if LEDGER.exists():
            for line in LEDGER.read_text().splitlines():
                if not line:
                    continue
                try:
                    import json
                    e = json.loads(line)
                except Exception:
                    continue
                runs += 1
                saved += int(e.get("saved") or 0)
                chars += int(e.get("chars") or 0)
        print(
            f"contained {runs} outputs · {fmt_size(chars)} raw → "
            f"~{saved // 4:,} tokens kept out of *resend accounting* "
            f"(~{fmt_size(saved)} archived; Grok still shows full result once)"
        )
    elif sub == "full":
        if len(argv) < 3:
            print("usage: swarmstate_cli.py full <call-id>")
            return 2
        arg = argv[2]
        if not OUT_DIR.exists():
            print(f"no outbox at {OUT_DIR}")
            return 1
        files = [f for f in OUT_DIR.iterdir()
                 if arg in f.name and f.suffix == ".txt"]
        if not files:
            print(f"no archive matching {arg!r}")
            return 1
        f = files[0]
        st = f.stat()
        lines = f.read_text(errors="replace").count("\n") + 1
        print(f"{f.name} — {fmt_size(st.st_size)} · {lines:,} lines · "
              f"sed -n 'A,Bp' {f}")
        digest = Path(str(f) + ".digest")
        if digest.exists():
            print(f"digest: {digest}")
    else:
        st = "ON" if cfg["enabled"] else "OFF"
        ks = kernel_status() if cfg["enabled"] else ""
        print(
            f"SwarmState: {st} | cap {cfg['capChars']:,} chars | "
            f"readLimit {cfg['readLimit']} | status "
            f"{'on' if cfg['injectStatus'] else 'off'} | "
            f"outbox {OUT_DIR} | state {STATE}"
        )
        if ks:
            print(ks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
