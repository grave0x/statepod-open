#!/usr/bin/env python3
"""PostToolUse: archive oversized tool results + ledger (prime ledger parity).

Grok docs: PostToolUse stdout is ignored — we only perform side effects.
The model still sees the full result; PreToolUse must prevent dumps.
Archived paths power `/statepod full` and `/statepod stats`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contain_lib import (  # noqa: E402
    MARKER,
    contain_text,
    enabled,
    extract_tool_result_text,
    ledger_record,
    read_cfg,
)


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not enabled():
        return 0

    cfg = read_cfg()
    cap = int(cfg.get("capChars") or 20000)
    tool = str(ev.get("toolName") or "tool")
    call_id = str(ev.get("toolUseId") or ev.get("tool_use_id") or ev.get("timestamp") or "x")
    text = extract_tool_result_text(ev.get("toolResult") if "toolResult" in ev else ev.get("tool_result"))
    if not text or text.startswith(MARKER) or len(text) <= cap:
        return 0

    out = contain_text(text, call_id, tool)
    ledger_record(
        call_id=call_id,
        tool=tool,
        chars=len(text),
        digest_chars=len(out["digest"]),
        cap=cap,
    )
    # Also drop a sidecar digest next to the archive for humans / sp full
    try:
        Path(out["path"] + ".digest").write_text(out["digest"], encoding="utf-8")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
