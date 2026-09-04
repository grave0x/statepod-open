#!/usr/bin/env python3
"""Shared DELTA-style containment helpers for the Grok SwarmState plugin.

Ports the prime-agent swarmstate.ts containText/ledger logic to stdlib Python.
Grok cannot rewrite tool *results* (PostToolUse stdout is ignored); this module
still archives oversized outputs and writes the containment ledger so /swarmstate
stats|full work. PreToolUse uses the same digest format when emitting guidance.
"""
from __future__ import annotations

from datetime import datetime as _dt

import json
import os
import re
from pathlib import Path
from typing import Any



def _ts() -> str:
    return _dt.utcnow().isoformat() + "Z"

HOME = Path.home()
STATE = HOME / ".swarmstate" / "omp.json"
OUT_DIR = HOME / ".swarmstate" / "grok" / "outbox"
LEDGER = HOME / ".swarmstate" / "grok" / "containment.jsonl"
GUIDED = HOME / ".swarmstate" / "grok" / "guided_sessions"
MARKER = "[swarmstate:contained]"
DIGEST_BUDGET = 700  # chars for head and tail each
DEFAULTS = {
    "enabled": False,
    "injectStatus": True,
    "capChars": 20000,
    "guidance": True,
    "readLimit": 200,
    "grepMax": 50,
}


def read_cfg() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(STATE.read_text())
        if isinstance(raw, dict):
            for k, default in DEFAULTS.items():
                if k in raw and type(raw[k]) is type(default):
                    cfg[k] = raw[k]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return cfg


def write_cfg(cfg: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(cfg, indent=2) + "\n")


def enabled() -> bool:
    return bool(read_cfg().get("enabled"))


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _cut(text: str, from_front: bool) -> str:
    if "\n" in text:
        lines = text.split("\n")
        kept = lines[:24] if from_front else lines[-24:]
        joined = "\n".join(kept)
        return joined[:DIGEST_BUDGET] if from_front else joined[-DIGEST_BUDGET:]
    return text[:DIGEST_BUDGET] if from_front else text[-DIGEST_BUDGET:]


def safe_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)[:120] or "x"


def contain_text(text: str, call_id: str, tool: str) -> dict[str, str]:
    """Archive full text; return DELTA digest + path (prime parity)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{safe_id(call_id)}.txt"
    try:
        path.write_text(text, encoding="utf-8", errors="replace")
    except OSError:
        pass
    chars = len(text)
    head = _cut(text, True)
    tail = _cut(text, False)
    middle = max(chars - (len(head) + len(tail)), 0)
    total_lines = text.count("\n") + (1 if text else 0)
    tail_start = max(1, total_lines - 23)
    digest = (
        f"{MARKER} {tool} {call_id} | {fmt_size(chars)} "
        f"({chars:,} chars, {total_lines:,} lines) contained to head+tail. "
        f"Full output archived: {path}. Re-read: sed -n '1,24p' {path}  ·  "
        f"sed -n '{tail_start},{total_lines}p' {path}  ·  "
        f"middle: sed -n '25,{max(tail_start - 1, 25)}p' {path}\n\n"
        f"{head}\n… {fmt_size(middle)} omitted …\n{tail}"
    )
    return {"digest": digest, "path": str(path)}


def ledger_record(
    *,
    call_id: str,
    tool: str,
    chars: int,
    digest_chars: int,
    cap: int,
) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _ts(),
        "id": call_id,
        "tool": tool,
        "chars": chars,
        "digestChars": digest_chars,
        "saved": max(chars - digest_chars, 0),
        "cap": cap,
    }
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def guidance_once(session_id: str, cfg: dict[str, Any] | None = None) -> str | None:
    """Return one-shot guidance text for this session, or None if already sent."""
    cfg = cfg or read_cfg()
    if not cfg.get("guidance"):
        return None
    GUIDED.mkdir(parents=True, exist_ok=True)
    sid = safe_id(session_id or "default")
    flag = GUIDED / sid
    if flag.exists():
        return None
    # Bound growth: drop oldest when too many session flags
    flags = sorted(GUIDED.iterdir(), key=lambda p: p.stat().st_mtime)
    while len(flags) > 200:
        try:
            flags.pop(0).unlink()
        except OSError:
            break
        flags = sorted(GUIDED.iterdir(), key=lambda p: p.stat().st_mtime)
    try:
        flag.write_text("1\n")
    except OSError:
        pass
    cap = int(cfg.get("capChars") or DEFAULTS["capChars"])
    return (
        f"[swarmstate] containment ON: dump-shaped reads/shell are rewritten before "
        f"they run; outputs over {cap:,} chars are archived under {OUT_DIR} "
        f"(ledger for /swarmstate stats). Prefer sed -n, grep -n -m, head/tail, "
        f"lean-ctx ctx_read(mode=signatures|map), or `swarmcli ask`/`swarmcli explain` — "
        f"never cat whole large files. Grok cannot rewrite tool results in-place "
        f"(unlike prime); PreToolUse hardening + archive is the containment path."
    )


def extract_tool_result_text(tool_result: Any) -> str:
    """Normalize PostToolUse toolResult payloads to plain text."""
    if tool_result is None:
        return ""
    if isinstance(tool_result, str):
        return tool_result
    if isinstance(tool_result, dict):
        for key in ("content", "output", "stdout", "text", "result"):
            if key in tool_result:
                return extract_tool_result_text(tool_result[key])
        return json.dumps(tool_result, ensure_ascii=False)
    if isinstance(tool_result, list):
        parts: list[str] = []
        for block in tool_result:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(tool_result)
