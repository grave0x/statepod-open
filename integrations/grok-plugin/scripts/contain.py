#!/usr/bin/env python3
"""CLI: contain a file or stdin into ~/.swarmstate/grok/outbox (manual prime-parity).

Usage:
  contain.py <path>              # archive file, print digest to stdout
  contain.py --id ID <path>
  cat big.txt | contain.py -     # stdin
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contain_lib import contain_text, ledger_record, read_cfg  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="DELTA-contain a dump into the Grok outbox")
    ap.add_argument("path", help="file path, or - for stdin")
    ap.add_argument("--id", default=None, help="archive id (default: basename or stdin)")
    ap.add_argument("--tool", default="contain.py")
    ap.add_argument("--ledger", action="store_true", help="also append containment ledger")
    args = ap.parse_args(argv[1:])

    if args.path == "-":
        text = sys.stdin.read()
        call_id = args.id or "stdin"
    else:
        p = Path(args.path)
        text = p.read_text(encoding="utf-8", errors="replace")
        call_id = args.id or p.name

    out = contain_text(text, call_id, args.tool)
    if args.ledger:
        cfg = read_cfg()
        ledger_record(
            call_id=call_id,
            tool=args.tool,
            chars=len(text),
            digest_chars=len(out["digest"]),
            cap=int(cfg.get("capChars") or 20000),
        )
    sys.stdout.write(out["digest"])
    if not out["digest"].endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
