#!/usr/bin/env python3
"""PreToolUse: when SwarmState is ON, harden dump-shaped tool inputs.

Closest Grok-side parity to prime's context containment: we cannot rewrite
tool *results*, so we stop dumps at the source and inject one-shot guidance
via additionalContext (delivered with the tool batch after the call).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Allow `python3 pretool_rewrite.py` from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from contain_lib import enabled, guidance_once, read_cfg  # noqa: E402

READ_TOOLS = {
    "read_file", "Read",
    "lean-ctx__ctx_read", "lean-ctx__read",
}
SHELL_TOOLS = {
    "run_terminal_command", "Bash",
    "lean-ctx__ctx_shell", "lean-ctx__shell",
}
GREP_TOOLS = {"grep", "Grep", "lean-ctx__ctx_search", "lean-ctx__search",
               "tree_sitter_query", "TreeSitterQuery", "ast_query", "ASTQuery"}
EDIT_TOOLS = {"Write", "write_file", "Edit", "edit_file", "edit",
              "ApplyRewrite", "apply_rewrite"}
FS_TOOLS = {"Mkdir", "mkdir", "Rmdir", "rmdir", "Remove",
            "remove_file", "RemoveFile", "Glob", "glob"}
WEB_TOOLS = {"ReadUrl", "ReadURL", "web_fetch"}


def _rewrite_read(inp: dict, limit: int) -> tuple[dict | None, str | None]:
    # Native read_file / Read
    if "limit" not in inp and "offset" not in inp and "start_line" not in inp:
        # lean-ctx mode=full without a line window → signatures (orientation)
        mode = str(inp.get("mode") or "")
        if mode in ("full", "raw", "anchored"):
            updated = dict(inp)
            updated["mode"] = "signatures"
            return updated, f"swarmstate: ctx_read mode={mode} → signatures (containment ON)"
        updated = dict(inp)
        updated["limit"] = limit
        return updated, f"swarmstate: capped read to {limit} lines (containment ON)"
    return None, None


def _rewrite_shell(cmd: str, limit: int, grep_max: int) -> tuple[str | None, str | None]:
    # bare cat/less/more PATH
    m = re.match(r"^\s*(cat|less|more)\s+(\S+)\s*$", cmd)
    if m and "|" not in cmd and "head" not in cmd:
        return f"head -n {limit} {m.group(2)}", f"swarmstate: rewrote `{m.group(1)}` → head -n {limit}"

    # python -c / node -e one-liners that dump files (common lean-ctx bypass)
    if re.search(r"python3?\s+-c\s+['\"].*open\(", cmd) and "head" not in cmd:
        return None, None  # too ambiguous to rewrite safely; guidance covers it

    # rg/grep without -m → add -m
    if re.match(r"^\s*(rg|grep)\b", cmd) and not re.search(r"(^|\s)-m\b|(^|\s)--max-count\b", cmd):
        if "|" not in cmd:
            return f"{cmd.rstrip()} -m {grep_max}", f"swarmstate: added -m {grep_max} to grep/rg"

    # find | xargs cat  (soft deny via ask? keep rewrite of trailing cat)
    m2 = re.match(r"^(.*\b)cat\s+(\S+)\s*$", cmd)
    if m2 and "head" not in cmd and "<<" not in cmd:
        # only rewrite if the command is essentially a dump of one path
        if re.match(r"^\s*cat\s+\S+\s*$", cmd):
            return f"head -n {limit} {m2.group(2)}", f"swarmstate: rewrote cat → head -n {limit}"

    return None, None


def _rewrite_grep(inp: dict, grep_max: int) -> tuple[dict | None, str | None]:
    if "head_limit" in inp or "max_results" in inp:
        return None, None
    updated = dict(inp)
    # native grep tool
    if "head_limit" not in updated:
        updated["head_limit"] = grep_max
    return updated, f"swarmstate: capped grep head_limit={grep_max}"

def _rewrite_edit(inp: dict, cap: int) -> tuple[dict | None, str | None]:
    """When edits or writes carry a large content field, archive the
    proposed content and return a note pointing to the archive. The
    model still gets a flag that its proposed write is large; the
    next tool call reads back the archive if it needs to verify."""
    content = inp.get("content") or inp.get("new_string") or inp.get("text")
    if not isinstance(content, str) or len(content) <= cap:
        return None, None
    # Archive and tell the model the full text is on disk
    import json as _json
    import subprocess as _sp
    archive_id = inp.get("path") or inp.get("file_path") or inp.get("fileName") or "write"
    archive_id = "edit-" + archive_id.replace("/", "_").lstrip("_")
    try:
        _sp.run(
            [sys.executable, str(Path(__file__).resolve().parent / "contain.py"),
             "--id", archive_id, "-"],
            input=content, text=True, capture_output=True, timeout=10,
        )
    except Exception:
        return None, None
    note = (f"swarmstate: write content {len(content):,} chars archived to "
            f"~/.swarmstate/grok/outbox/{archive_id}.txt (containment ON). "
            f"Use sed -n on the archive if you need to verify; the write "
            f"itself is unchanged — the model still sees its intended edit.")
    return None, note  # never rewrite the user's intended edit; only annotate


def _rewrite_web(inp: dict, cap: int) -> tuple[dict | None, str | None]:
    """Web fetches are also dump-shaped (full pages in stdout). Add a
    max_tokens / cap so the model sees a summary, not a full HTML dump."""
    updated = None
    note = None
    for cap_field in ("max_tokens", "maxTokens", "max_chars", "maxChars"):
        if cap_field in inp:
            return None, None  # already capped
    if "max_tokens" not in inp and "max_tokens" not in (updated or {}):
        updated = dict(inp)
        updated["max_tokens"] = 4000
        note = ("swarmstate: web_fetch capped to max_tokens=4000 (containment ON) "
                "— full page is one fetch+headless call away if needed")
    return updated, note


def _rewrite_grep_adv(inp: dict, grep_max: int) -> tuple[dict | None, str | None]:
    """tree_sitter / ast_query: cap the result count to avoid a 1000-node
    tree dump in one call. The kernel symbol summary should usually be
    used instead (lean-ctx__symbol_summary if available, else the model
    reasons about the AST_QUERY response with a smaller max_results)."""
    if "max_results" in inp or "limit" in inp or "max_nodes" in inp:
        return None, None
    updated = dict(inp)
    updated["max_results"] = grep_max
    return updated, (f"swarmstate: ast_query/tree_sitter capped to "
                     f"max_results={grep_max} (containment ON) — prefer "
                     f"lean-ctx__symbol_summary or SYMBOL_SUMMARY for "
                     f"orientation; ast_query is for narrowing once you "
                     f"already know the symbol/edge you want)")






def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not enabled():
        print("{}")
        return 0

    cfg = read_cfg()
    limit = int(cfg.get("readLimit") or 200)
    grep_max = int(cfg.get("grepMax") or 50)
    tool = str(ev.get("toolName") or "")
    inp = ev.get("toolInput") or {}
    if not isinstance(inp, dict):
        print("{}")
        return 0

    updated = None
    note = None

    if tool in READ_TOOLS:
        updated, note = _rewrite_read(inp, limit)
    elif tool in SHELL_TOOLS:
        cmd = str(inp.get("command") or "")
        new_cmd, note = _rewrite_shell(cmd, limit, grep_max)
        if new_cmd is not None:
            updated = dict(inp)
            updated["command"] = new_cmd
    elif tool in GREP_TOOLS:
        # AST / tree-sitter / ast_query all want a result cap, not grep -m
        if tool in ("tree_sitter_query", "TreeSitterQuery", "ast_query", "ASTQuery"):
            updated, note = _rewrite_grep_adv(inp, grep_max)
        else:
            updated, note = _rewrite_grep(inp, grep_max)
    elif tool in EDIT_TOOLS:
        # Write / Edit tools: never change the user's intended edit, but
        # archive large proposed content and annotate so the model knows
        # where the full text lives.
        updated, note = _rewrite_edit(inp, int(cfg.get("capChars") or 20000))
    elif tool in WEB_TOOLS:
        updated, note = _rewrite_web(inp, int(cfg.get("capChars") or 20000))
    elif tool in FS_TOOLS:
        # Filesystem ops can't dump content; nothing to rewrite.
        pass
    elif tool in ("use_tool", "CallMcpTool"):
        # MCP dispatcher — inspect the nested tool name (best effort)
        nested = str(inp.get("tool_name") or inp.get("name") or "")
        if nested in READ_TOOLS:
            updated, note = _rewrite_read(inp.get("arguments") or inp, limit)
        elif nested in GREP_TOOLS:
            if nested in ("tree_sitter_query", "TreeSitterQuery", "ast_query", "ASTQuery"):
                updated, note = _rewrite_grep_adv(inp.get("arguments") or inp, grep_max)
            else:
                updated, note = _rewrite_grep(inp.get("arguments") or inp, grep_max)
        elif nested in WEB_TOOLS:
            updated, note = _rewrite_web(inp.get("arguments") or inp, int(cfg.get("capChars") or 20000))

    sid = str(ev.get("sessionId") or ev.get("cwd") or "default")
    guide = guidance_once(sid, cfg)
    contexts = [c for c in (note, guide) if c]
    additional = "\n".join(contexts) if contexts else None

    if updated is None and not additional:
        print("{}")
        return 0

    hso: dict = {"hookEventName": "PreToolUse"}
    if updated is not None:
        hso["updatedInput"] = updated
    if additional:
        hso["additionalContext"] = additional[:10000]
    print(json.dumps({"hookSpecificOutput": hso}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
