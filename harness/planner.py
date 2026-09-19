"""Planner: user query -> kernel Plan.

Two backends:
  - mock   (default): deterministic keyword rules. Proves the loop.
  - ollama : Qwen 2.5 Coder via Ollama's OpenAI-compatible API. Needs
             `ollama serve` running and a model pulled, e.g.
             `ollama pull qwen2.5-coder:7b`.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from dag import validate_dag, schedule as dag_schedule, summarize as dag_summarize, build_dag

# Local (ollama) plan generation guardrails: hard token cap + socket
# timeout so a runaway generation can never hang a task forever. A compact
# plan is 60-150 completion tokens; 512 is generous headroom.
OLLAMA_MAX_TOKENS = 512
OLLAMA_TIMEOUT = 150

# Plan op constants (from statepod.py)
SP_OP_READ = 0
SP_OP_WRITE = 1
SP_OP_GREP = 2
SP_OP_DIFF = 3
SP_OP_STATUS = 4
SP_OP_EXECUTE = 5
SP_OP_AST_PARSE = 6
SP_OP_AST_QUERY = 7
SP_OP_SYMBOL_SUMMARY = 8
SP_CONTEXT_DELTA = 0
SP_CONTEXT_TARGETED = 1
SP_CONTEXT_FULL = 2
SP_CONTEXT_SYMBOLIC = 3

VALID_TYPES = {
    "READ": SP_OP_READ, "WRITE": SP_OP_WRITE, "GREP": SP_OP_GREP,
    "DIFF": SP_OP_DIFF, "STATUS": SP_OP_STATUS, "EXECUTE": SP_OP_EXECUTE,
    "AST_PARSE": SP_OP_AST_PARSE, "AST_QUERY": SP_OP_AST_QUERY,
    "SYMBOL_SUMMARY": SP_OP_SYMBOL_SUMMARY,
}


def _quoted(text: str) -> list[str]:
    return re.findall(r"[\"']([^\"']+)[\"']", text)


def _path_from(query: str) -> str | None:
    for p in _quoted(query):
        if re.search(r"\.\w+$", p):
            return p
    m = re.search(r"\b([\w./-]+\.(?:c|h|py|rs|go|js|ts|md|txt|toml|json))\b", query)
    return m.group(1) if m else None


def _mock_edit(query: str, q: str) -> dict | None:
    """Deterministic full-file edit engine for the code-edit families.

    Applies the edit to the shared FIXTURE content (same source of truth
    build_repo() writes and the corpus oracles check) and returns a WRITE
    op with the full new content — the exact behaviour the prompts teach
    real models. Returns None when the query is not a recognized edit, so
    plan_mock falls through to the keyword router.
    """
    import corpus  # local import: corpus is harness-side, planner must
    # stay importable by py/ consumers without the harness package
    m = re.search(r'rename "([^"]+)" to "([^"]+)" in (\S+)', query)
    if m:
        old, new, path = m.group(1), m.group(2), m.group(3)
        if path not in corpus.FIXTURE:
            return None
        content = re.sub(rf"\b{re.escape(old)}\b", new, corpus.FIXTURE[path])
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    m = re.search(r'add a function "([^"]+)" to (\S+)', query)
    if m:
        sig, path = m.group(1), m.group(2)
        if path not in corpus.FIXTURE:
            return None
        content = corpus.FIXTURE[path]
        if not content.endswith("\n"):
            content += "\n"
        content += sig + " { return 0; }\n"
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    m = re.search(r'remove the unused \w+ "([^"]+)" from (\S+)', query)
    if m:
        name, path = m.group(1), m.group(2)
        if path not in corpus.FIXTURE:
            return None
        content = "\n".join(l for l in corpus.FIXTURE[path].splitlines()
                             if name not in l) + "\n"
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    m = re.search(r'add "([^"]+)" to (\S+)', query)
    if m:
        text, path = m.group(1), m.group(2)
        if path not in corpus.FIXTURE:
            return None
        content = corpus.FIXTURE[path]
        if not content.endswith("\n"):
            content += "\n"
        content += text + "\n"
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    m = re.search(r'add a test function "([^"]+)" to (\S+)', query)
    if m:
        name, path = m.group(1), m.group(2)
        if path not in corpus.FIXTURE:
            return None
        content = corpus.FIXTURE[path]
        if not content.endswith("\n"):
            content += "\n"
        content += f"int {name}(void) {{ return 0; }}\n"
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    m = re.search(r'add a doc comment to the function "([^"]+)" in (\S+)', query)
    if m:
        name, path = m.group(1), m.group(2)
        if path not in corpus.FIXTURE:
            return None
        lines = corpus.FIXTURE[path].splitlines()
        idx = next((i for i, l in enumerate(lines) if name + "(" in l), None)
        if idx is None:
            return None
        lines.insert(idx, f"// {name}: documentation")
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": "\n".join(lines) + "\n"}],
                "strategy": "DELTA"}
    m = re.search(r'convert "([^"]+)" to "([^"]+)" in (\S+)', query)
    if m:
        old, new, path = m.group(1), m.group(2), m.group(3)
        if path not in corpus.FIXTURE or old not in corpus.FIXTURE[path]:
            return None
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": corpus.FIXTURE[path].replace(old, new)}],
                "strategy": "DELTA"}
    # canonical fallback: 'find unused variables in src/math.c' (no quoted
    # symbol) -> drop the unused-* declaration line from the named file
    if "unused" in q:
        path = next((w for w in corpus.FIXTURE
                     if w.endswith((".c", ".py")) and w in q), None)
        if path is None:
            return None
        content = "\n".join(l for l in corpus.FIXTURE[path].splitlines()
                             if "unused" not in l) + "\n"
        return {"ops": [{"type": "WRITE", "path": path,
                         "content": content}], "strategy": "DELTA"}
    return None


def plan_mock(query: str, summary: str | None = None) -> dict:
    q = query.lower()
    # code-edit families (rename/add/remove/readme/test/docstring/style):
    # the keyword router used to misroute these ('rename ... count' -> wc
    # via the count branch; 'add a function' -> AST_PARSE via the function
    # branch; 'find unused variables' -> GREP via the find branch), so the
    # mock suite was WEAK on the same tasks and the registry learned False
    # for those signatures. The mock now performs the edit literally as a
    # full-file WRITE against the shared FIXTURE — the exact behaviour the
    # prompts teach real models — and the corpus oracles then pass.
    edit = _mock_edit(query, q)
    if edit is not None:
        return edit
    if any(w in q for w in ("grep", "search", "find")):
        pattern = _quoted(query)[0] if _quoted(query) else "TODO"
        return {"ops": [{"type": "GREP", "pattern": pattern, "target": ""}],
                "strategy": "DELTA"}
    if "status" in q:
        return {"ops": [{"type": "STATUS"}], "strategy": "DELTA"}
    if "diff" in q:
        path = _path_from(query)
        op = {"type": "DIFF", "path": path} if path else {"type": "DIFF"}
        return {"ops": [op], "strategy": "DELTA"}
    if any(w in q for w in ("write", "create", "update", "edit")):
        parts = _quoted(query)
        path = parts[0] if parts else "note.txt"
        content = parts[1] if len(parts) > 1 else f"{query}\n"
        return {"ops": [{"type": "WRITE", "path": path, "content": content}],
                "strategy": "DELTA"}
    if any(w in q for w in ("read", "show", "open", "cat")):
        path = _path_from(query) or "README.md"
        return {"ops": [{"type": "READ", "path": path}], "strategy": "DELTA"}
    if any(w in q for w in ("function", "signature", "struct", "symbol", "ast")):
        path = _path_from(query) or "src/math.c"
        return {"ops": [{"type": "AST_PARSE", "path": path}], "strategy": "DELTA"}
    if any(w in q for w in ("document", "explain", "summarize", "describe",
                            "what does", "what is")):
        # documentation layer: symbol summaries instead of raw content
        path = _path_from(query)
        op = {"type": "SYMBOL_SUMMARY"}
        if path:
            op["path"] = path
        return {"ops": [op], "strategy": "DELTA"}
    if any(w in q for w in ("count", "wc", "lines")):
        path = _path_from(query) or "."
        return {"ops": [{"type": "EXECUTE", "command": f"wc -l {path}"}],
                "strategy": "DELTA"}
    # fallback: show repo status + a delta summary of the cached state
    return {"ops": [{"type": "STATUS"}], "strategy": "DELTA",
            "note": "fallback: unrecognized query -> status"}


def op_signature(op: dict) -> str:
    """Registry key for one op (mirrors the kernel's auto-record keys)."""
    otype = str(op.get("type", "")).upper()
    if otype == "GREP":
        return "GREP:repo" if not op.get("target") else "GREP:file"
    if otype == "SYMBOL_SUMMARY":
        return "SYMBOL_SUMMARY:file" if op.get("path") else "SYMBOL_SUMMARY:repo"
    return otype


def plan_signature(ops: list[dict]) -> str:
    """Plan-level key for feedback: sorted op signatures joined with '+'."""
    return "+".join(sorted(set(op_signature(o) for o in ops))) or "EMPTY"


# Spec 9.3: ≥3 samples before STRAT: overrides the op-shape heuristic
# (aligned with pick_model_rates / build-plan-v1).
STRAT_MIN_SAMPLES = 3


# Cost order for tie-breaking (cheapest first): the registry should not
# upgrade a task to an expensive strategy without evidence. SYMBOLIC
# sits between DELTA and TARGETED (doc-layer §6.3).
_STRAT_COST = {"DELTA": 0, "SYMBOLIC": 1, "TARGETED": 2, "FULL": 3}


def registry_strategy(registry, signature: str, default: str) -> str:
    """Spec 9.3: registry-driven strategy selection.

    The Orchestrator.feedback() loop records semantic y/n against
    STRAT:<plan-signature>:<strategy> keys, so the registry learns which
    context strategy actually wins per task shape.

    Selection:
      - strategies with < STRAT_MIN_SAMPLES are unproven -> ignored
      - proven winners (rate > 0.5) compete; best rate wins, ties go to
        more samples then cheaper strategy
      - if nothing is proven (cold start) -> `default` (op-shape
        heuristic) — deliberately NOT the spec's naive FULL fallback:
        FULL is the most expensive strategy and should be earned by
        evidence or an explicit request, not by ignorance
      - if every proven strategy is bad (all rates <= 0.5) -> least-bad
        wins, so the loop keeps learning instead of stalling.
    """
    if registry is None:
        return default
    proven = []  # (rate, strategy, samples)
    for strat in VALID_STRATEGIES:
        key = f"STRAT:{signature}:{strat}"
        try:
            timing = registry.registry_timing(key)
            if timing["fb_samples"] < STRAT_MIN_SAMPLES:
                continue
            rate = registry.success_rate(key)
        except Exception:
            continue
        proven.append((rate, strat, timing["fb_samples"]))
    if not proven:
        return default
    pool = [p for p in proven if p[0] > 0.5] or proven
    pool.sort(key=lambda p: (-p[0], -p[2], _STRAT_COST[p[1]]))
    return pool[0][1]


def decide_strategy(ops: list[dict], requested: str | None = None,
                    registry=None) -> tuple[str, list[str] | None]:
    """Context-containment strategy: registry-driven (spec 9.3) with an
    op-shape heuristic fallback for the cold start.

    Selection order:
      1. an explicit FULL request is always honored (complex tasks ask
         for full context deliberately)
      2. if the registry has proven STRAT: feedback for this plan
         signature, the best-proven strategy wins (registry_strategy)
      3. otherwise the op-shape heuristic:
         - plans asking for SYMBOL_SUMMARY -> SYMBOLIC (summary diet)
         - reads that need content -> TARGETED with exactly those paths
         - multi-op plans touching the same file -> TARGETED on that file
         - everything else (writes, executes, diffs, status) -> DELTA so
           the LLM never sees files it didn't ask to read.
    Returns (strategy, target_paths).
    """
    if requested == "FULL":
        return "FULL", None
    reads = [o.get("path") for o in ops
             if o.get("type") == "READ" and o.get("path")]
    mutating = [o for o in ops
                if o.get("type") in ("WRITE", "EXECUTE", "DIFF", "STATUS")]
    if any(o.get("type") == "SYMBOL_SUMMARY" for o in ops):
        # SYMBOLIC (doc-layer §6.1): a plan that asks for symbol summaries
        # is understanding/documenting/refactoring code — keep the LLM on
        # the summary diet (cheaper than raw content, no files leak).
        default = "SYMBOLIC"
        default_targets = None
    elif reads and not mutating and len(reads) <= 4:
        default = "TARGETED"
        default_targets = reads
    else:
        # complexity bump: >3 ops all touching one path -> TARGETED on it
        paths = [o.get("path") for o in ops if o.get("path")]
        if len(ops) > 3 and paths and len(set(paths)) == 1:
            default = "TARGETED"
            default_targets = [paths[0]]
        else:
            default = "DELTA"
            default_targets = None
    chosen = registry_strategy(registry, plan_signature(ops), default)
    if chosen == default:
        return default, default_targets
    if chosen == "TARGETED":
        # registry proved TARGETED best: target the plan's read paths.
        # No paths to target -> DELTA (same kernel behavior, honest log).
        targets = reads or default_targets
        return (chosen, targets) if targets else ("DELTA", None)
    return chosen, None


# Per-op required fields (schema for the confidence gate).
PLAN_KEYS = {
    "READ": ["path"],
    "WRITE": ["path", "content"],
    "GREP": ["pattern"],
    "DIFF": [],
    "STATUS": [],
    "EXECUTE": ["command"],
    "AST_PARSE": ["path"],
    "AST_QUERY": ["path", "pattern"],
    "SYMBOL_SUMMARY": [],   # all fields optional (path/pattern/target)
}

VALID_STRATEGIES = ("DELTA", "TARGETED", "FULL", "SYMBOLIC")

# last LLM API usage (prompt/completion tokens) from _chat_completion,
# captured for the task suite's token-economy reporting.
last_usage: dict = {}


def normalize_plan(plan: dict) -> dict:
    """Canonical plan shape. Accepts the spec's `context_strategy` naming
    and case variations; returns dict with 'strategy' + upper-cased ops."""
    plan = dict(plan or {})
    strategy = str(plan.get("strategy") or plan.get("context_strategy")
                   or "DELTA").upper()
    plan["strategy"] = strategy if strategy in VALID_STRATEGIES else "DELTA"
    ops = []
    for raw in plan.get("ops") or []:
        if isinstance(raw, dict):
            op = dict(raw)
            op["type"] = str(op.get("type", "")).upper()
            # LLMs sometimes emit EXECUTE command as an argv list
            # (e.g. ["wc", "-l", "a.c"]) — join into a plain string so the
            # kernel's allowlist sees the command, not the repr.
            cmd = op.get("command")
            if isinstance(cmd, list):
                op["command"] = " ".join(str(t) for t in cmd)
            ops.append(op)
    plan["ops"] = ops
    return plan


def validate_plan(plan: dict) -> tuple[bool, list[str]]:
    """Strict schema check for the confidence gate: every op must have a
    known type and its required fields, and the strategy must be valid.
    Returns (ok, errors)."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return False, ["plan is not a JSON object"]
    ops = plan.get("ops")
    if not isinstance(ops, list) or not ops:
        return False, ["plan.ops must be a non-empty list"]
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            errors.append(f"op[{i}] is not an object")
            continue
        otype = str(op.get("type", "")).upper()
        if otype not in VALID_TYPES:
            errors.append(f"op[{i}].type '{op.get('type')}' is unknown")
            continue
        for key in PLAN_KEYS.get(otype, []):
            if not op.get(key):
                errors.append(f"op[{i}].{key} is required for {otype}")
    strategy = str(plan.get("strategy", "")).upper()
    if strategy and strategy not in VALID_STRATEGIES:
        errors.append(f"strategy '{plan.get('strategy')}' is invalid")
    return (not errors, errors)


def _extract_json(text: str) -> dict:
    """Parse an LLM reply, tolerating markdown fences and surrounding
    prose. Finds the first balanced JSON object."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON object in reply")


def _config_path() -> Path:
    return Path.home() / ".prime" / "agent"


def _json_at(path: Path, keys: list[str]):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    for k in keys:
        if not isinstance(data, dict) or k not in data:
            return None
        data = data[k]
    return data


def deepseek_config() -> tuple[str, str | None]:
    """baseUrl + apiKey for DeepSeek, reusing prime-agent's own config
    (models.json provider entry + auth.json key) with env fallbacks."""
    base = os.environ.get("STATEPOD_DEEPSEEK_BASE")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not base:
        base = _json_at(_config_path() / "models.json",
                        ["providers", "deepseek", "baseUrl"])
    if not key:
        key = _json_at(_config_path() / "auth.json", ["deepseek", "key"])
    return (base or "https://api.deepseek.com/v1"), key


def _chat_completion(base: str, api_key: str, model: str,
                     system: str, user: str, want_json: bool) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
    }
    if want_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    # direct connection (localhost gateway); bypass any proxy env vars
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    last_usage.update(data.get("usage") or {})
    return data


def _openai_plan(base: str, api_key: str | None, model: str,
                 query: str, summary: str | None) -> dict:
    """Shared OpenAI-compatible planner path (FreeToken, llama-server, …)."""
    system = _planner_system(summary)
    key = api_key or ""
    try:
        data = _chat_completion(base, key, model, system, query, want_json=True)
    except Exception:
        data = _chat_completion(base, key, model, system, query, want_json=False)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("planner API returned no choices: " + str(data)[:200])
    text = choices[0]["message"]["content"]
    return _validate_plan(_extract_json(text))


# OpenAI-compatible provider presets. Each entry is a thin named default
# for base URL / key env / model. Override with STATEPOD_<NAME>_BASE,
# STATEPOD_<NAME>_MODEL, and the listed key env vars.
# Aliases (e.g. llama -> llamacpp) live in OPENAI_BACKEND_ALIASES.
_OPENAI_PRESETS: dict[str, dict] = {
    # Generic / cloud
    "openai": {
        "base": "https://api.openai.com/v1",
        "key_envs": ("OPENAI_API_KEY", "STATEPOD_OPENAI_KEY"),
        "default_model": None,  # require --model
        "require_key": True,
        "hint": "set OPENAI_API_KEY and --model (or STATEPOD_OPENAI_BASE)",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key_envs": ("OPENROUTER_API_KEY", "STATEPOD_OPENROUTER_KEY"),
        "default_model": None,
        "require_key": True,
        "hint": "set OPENROUTER_API_KEY and --model (e.g. qwen/qwen3-coder)",
    },
    # Local servers (OpenAI-shaped)
    "freetoken": {
        "base": "http://127.0.0.1:1919/v1",
        "key_envs": ("FREETOKEN_API_KEY", "STATEPOD_FREETOKEN_KEY"),
        "default_model": None,
        "require_key": False,
        "hint": "start `ft serve --model <id>` (default :1919)",
    },
    "llamacpp": {
        "base": "http://127.0.0.1:8080/v1",
        "key_envs": ("STATEPOD_LLAMACPP_KEY", "LLAMACPP_API_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "start `llama-server -m <gguf> --port 8080`",
    },
    "vllm": {
        "base": "http://127.0.0.1:8000/v1",
        "key_envs": ("VLLM_API_KEY", "STATEPOD_VLLM_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "start vLLM with an OpenAI server on :8000",
    },
    "sglang": {
        "base": "http://127.0.0.1:30000/v1",
        "key_envs": ("SGLANG_API_KEY", "STATEPOD_SGLANG_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "start SGLang OpenAI server on :30000",
    },
    "lmstudio": {
        "base": "http://127.0.0.1:1234/v1",
        "key_envs": ("LMSTUDIO_API_KEY", "STATEPOD_LMSTUDIO_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "enable LM Studio local server (:1234)",
    },
    "koboldcpp": {
        "base": "http://127.0.0.1:5001/v1",
        "key_envs": ("KOBOLDCPP_API_KEY", "STATEPOD_KOBOLDCPP_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "start KoboldCpp with OpenAI API on :5001",
    },
    "tabbyapi": {
        "base": "http://127.0.0.1:5000/v1",
        "key_envs": ("TABBYAPI_API_KEY", "STATEPOD_TABBYAPI_KEY"),
        "default_model": "local",
        "require_key": False,
        "hint": "start TabbyAPI on :5000",
    },
}

OPENAI_BACKEND_ALIASES: dict[str, str] = {
    "llama": "llamacpp",
}

OPENAI_BACKEND_NAMES: tuple[str, ...] = tuple(
    sorted(set(_OPENAI_PRESETS) | set(OPENAI_BACKEND_ALIASES))
)


def resolve_openai_backend(name: str) -> str:
    """Canonical preset name (aliases resolved). Raises KeyError if unknown."""
    canon = OPENAI_BACKEND_ALIASES.get(name, name)
    if canon not in _OPENAI_PRESETS:
        raise KeyError(name)
    return canon


def openai_backend_config(name: str) -> tuple[str, str | None, dict]:
    """Return (base_url, api_key_or_None, preset_dict) for a named backend."""
    canon = resolve_openai_backend(name)
    preset = _OPENAI_PRESETS[canon]
    env_prefix = f"STATEPOD_{canon.upper()}_"
    base = os.environ.get(env_prefix + "BASE") or preset["base"]
    key = ""
    for env in preset.get("key_envs") or ():
        key = os.environ.get(env) or key
    return base, (key or None), preset


def plan_openai_backend(name: str, query: str, summary: str | None,
                        model: str | None = None) -> dict:
    """Plan via any OpenAI-compatible preset (local server or cloud gateway)."""
    canon = resolve_openai_backend(name)
    base, key, preset = openai_backend_config(canon)
    env_prefix = f"STATEPOD_{canon.upper()}_"
    model = (model
             or os.environ.get(env_prefix + "MODEL")
             or preset.get("default_model")
             or "")
    if not model:
        raise RuntimeError(
            f"{canon} planner needs --model (or {env_prefix}MODEL); "
            + preset.get("hint", ""))
    if preset.get("require_key") and not key:
        envs = " / ".join(preset.get("key_envs") or ())
        raise RuntimeError(f"{canon} planner needs an API key ({envs})")
    return _openai_plan(base, key, model, query, summary)


def freetoken_config() -> tuple[str, str | None]:
    """OpenAI-compatible FreeToken server (ft serve / ft daemon)."""
    base, key, _ = openai_backend_config("freetoken")
    return base, key


def plan_freetoken(query: str, summary: str | None,
                   model: str | None = None) -> dict:
    """Planner via FreeToken (FlashML) OpenAI-compatible HTTP API."""
    return plan_openai_backend("freetoken", query, summary, model=model)


def llamacpp_config() -> tuple[str, str | None]:
    """OpenAI-compatible llama-server (llama.cpp)."""
    base, key, _ = openai_backend_config("llamacpp")
    return base, key


def plan_llamacpp(query: str, summary: str | None,
                  model: str | None = None) -> dict:
    """Planner via llama-server OpenAI-compatible HTTP API."""
    return plan_openai_backend("llamacpp", query, summary, model=model)


def plan_deepseek(query: str, summary: str | None,
                  model: str | None = None,
                  base: str | None = None, api_key: str | None = None,
                  full: bool = False) -> dict:
    """Planner via DeepSeek (cloud) — used while the local model pulls.

    full=False (default): StatePod containment — the model sees only the
    repo STATE SUMMARY, never files (the kernel holds the context).
    full=True: NAIVE BASELINE — the model sees the entire repository
    contents (a normal full-context call), for apples-to-apples token/cost
    comparison on the same tasks. Same model, same plan schema, same
    execution; only the context the LLM is given differs."""
    base, cfg_key = deepseek_config()
    api_key = api_key or cfg_key
    if not api_key:
        raise RuntimeError("no DeepSeek API key (set DEEPSEEK_API_KEY or "
                           "STATEPOD_DEEPSEEK_BASE)")
    model = model or "deepseek-v4-flash"
    if full:
        context_preamble = (
            "You see the ENTIRE repository contents below (naive full-context "
            "baseline — no context containment). Still reply with a minimal "
            "batch PLAN of kernel ops; the kernel executes it.")
        context_label = "Full repository contents:"
    else:
        context_preamble = (
            "The C kernel executes BATCH operations locally; the model only "
            "sees summaries, never files.")
        context_label = "Repo state summary:"
    system = (
        "You are StatePod's planner. " + context_preamble + "\n"
        "Available op types: READ (path, line_start, line_end), "
        "WRITE (path, content — content is the EXACT literal text to write; "
        "never a shell command, sed expression like s/x/y/g, or placeholder "
        "like <new_content>; for edits emit the FULL new file content. "
        "Example: rename x to count -> WRONG content \"s/x/count/g\", "
        "RIGHT content = the whole file text with the edit applied), "
        "GREP (pattern, target), DIFF (path), STATUS, "
        "EXECUTE (single command, NO shell syntax: no pipes |, no redirection >, "
        "no ;, no quotes; space-separated tokens only; read-only binaries "
        "ls cat pwd wc head tail find grep git echo date stat cmp dirname basename; "
        "paths relative to the repo root; prefer READ over \"cat\"), "
        "AST_PARSE (path; structural summary, pattern='sexp' for the raw tree), "
        "AST_QUERY (path + tree-sitter pattern — pattern is REQUIRED; if you "
        "have no pattern to query, use SYMBOL_SUMMARY with path/target instead; "
        "valid C nodes: function_definition, declaration, init_declarator, "
        "identifier, parameter_list, call_expression, comment, "
        "preproc_include — e.g. '(declaration (init_declarator (identifier) "
        "@name))' or '(function_definition) @func' to list functions), "
        "SYMBOL_SUMMARY (path optional = whole repo; pattern = name substring; "
        "target = exact symbol; compact function/struct/enum summaries with "
        "signatures and in-file call edges — prefer it over READ when the task "
        "is about understanding or documenting code; use it to list or "
        "enumerate the functions/symbols in a file).\n"
        "Context strategies: DELTA (summaries only), SYMBOLIC (symbol summaries instead of raw content — best for understanding/documenting code), TARGETED (plus target_paths), FULL.\n"
        f"{context_label}\n{summary or '(empty)'}\n"
        "Reply with ONLY a JSON object, no prose, e.g. "
        '{"ops":[{"type":"GREP","pattern":"TODO","target":""}],"strategy":"DELTA"}'
    )
    try:
        data = _chat_completion(base, api_key, model, system, query, want_json=True)
    except Exception:
        # gateway/model may not support json_object mode; retry plain
        data = _chat_completion(base, api_key, model, system, query, want_json=False)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("planner API returned no choices: "
                           + str(data)[:200])
    text = choices[0]["message"]["content"]
    return _validate_plan(_extract_json(text))


def _planner_system(summary: str | None) -> str:
    return (
        "You are StatePod's planner. The C kernel executes BATCH operations "
        "locally; the model only sees summaries, never files.\n"
        "Available op types: READ (path, line_start, line_end), WRITE (path, content), "
        "GREP (pattern, target), DIFF (path), STATUS, "
        "EXECUTE (single command, NO shell syntax: no pipes |, no redirection >, "
        "no ;, no quotes; space-separated tokens only; read-only binaries "
        "ls cat pwd wc head tail find grep git echo date stat cmp dirname basename; "
        "paths relative to the repo root; prefer READ over \"cat\"), "
        "AST_PARSE (path; structural summary, pattern='sexp' for the raw tree), "
        "AST_QUERY (path + tree-sitter pattern — pattern is REQUIRED; if you "
        "have no pattern to query, use SYMBOL_SUMMARY with path/target instead; "
        "valid C nodes: function_definition, declaration, init_declarator, "
        "identifier, parameter_list, call_expression, comment, "
        "preproc_include — e.g. '(declaration (init_declarator (identifier) "
        "@name))' or '(function_definition) @func' to list functions), "
        "SYMBOL_SUMMARY (path optional = whole repo; pattern = name substring; "
        "target = exact symbol; compact function/struct/enum summaries with "
        "signatures and in-file call edges — prefer it over READ when the task "
        "is about understanding or documenting code; use it to list or "
        "enumerate the functions/symbols in a file).\n"
        "WRITE content is the EXACT literal text to write to the file — never "
        "a shell command, never a sed expression like s/x/y/g, never a "
        "placeholder like <new_content>; when a task edits existing code, "
        "emit the FULL new file content with the edit applied. Example: "
        "rename x to count in a one-line file -> WRONG "
        '{"type":"WRITE","path":"a.c","content":"s/x/count/g"} ; '
        'RIGHT {"type":"WRITE","path":"a.c","content":"int count = 3;\\n"}. '
        "VERIFY before emitting: the WRITE content must differ from the "
        "read file EXACTLY by the requested edit — same code, same "
        "comments, same blank lines, plus the change. Do NOT echo the "
        "file back unchanged, do NOT delete or condense unrelated code. "
        "Context strategies: DELTA (summaries only), SYMBOLIC (symbol summaries instead of raw content — best for understanding/documenting code), TARGETED (plus target_paths), FULL.\n"
        f"Repo state summary:\n{summary or '(empty)'}\n"
        "Reply with ONLY a JSON object, no prose, e.g. "
        '{"ops":[{"type":"GREP","pattern":"TODO","target":""}],"strategy":"DELTA"}'
    )

def plan_ollama(query: str, summary: str | None,
                model: str = "qwen2.5-coder:7b",
                base: str | None = None,
                num_gpu_layers: int = 0,
                num_ctx: int = 0) -> dict:
    """Local plan via Ollama's OpenAI-compat endpoint.

    num_gpu_layers > 0 (or env SP_OLLAMA_NGL) adds options.num_gpu so
    llama.cpp offloads that many layers to the GPU (e.g. a 2 GB card:
    qwen2.5-coder:1.5b fits fully at default; a 7B is 0 layers by
    default because the KV cache + compute buffers already eat most of
    2 GB — force 5-7 layers to trade VRAM for a modest speedup).

    num_ctx > 0 (or env SP_OLLAMA_NUM_CTX) caps the context window.
    Plans are short JSON over a small summary: 1024 is generous and
    cuts the KV cache from 224 MiB (4096) to 56 MiB, freeing VRAM for
    more offloaded layers. Never go below ~768 with the full planner
    system prompt.
    """
    base = base or os.environ.get("STATEPOD_OLLAMA", "http://localhost:11434/v1")
    ngl = num_gpu_layers or int(os.environ.get("SP_OLLAMA_NGL", "0") or 0)
    nctx = num_ctx or int(os.environ.get("SP_OLLAMA_NUM_CTX", "0") or 0)
    system = _planner_system(summary)
    def _post():
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
            "temperature": 0.1,
            # hard cap: a compact plan is well under 512 tokens; bounds a
            # runaway generation so a task can never hang the suite forever
            "max_tokens": OLLAMA_MAX_TOKENS,
        }
        endpoint = base + "/chat/completions"
        if ngl > 0 or nctx > 0:
            # The /v1 OpenAI-compat endpoint IGNORES options (verified
            # live: CONTEXT stayed 4096 with num_ctx=1024). Route
            # options-carrying requests to the native /api/chat, which
            # honors them, and unwrap the different response shape.
            opts = {}
            if ngl > 0:
                opts["num_gpu"] = ngl   # llama.cpp -ngl equivalent
            if nctx > 0:
                opts["num_ctx"] = nctx  # KV cache size in tokens
            body["options"] = opts
            body["stream"] = False
            endpoint = base.replace("/v1", "") + "/api/chat"
        req = urllib.request.Request(endpoint,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    # No response_format for the local model: qwen2.5-coder emits JSON (or
    # markdown-wrapped JSON) fine in plain mode and _extract_json strips
    # prose. json_object mode is a risk multiplier on small models.
    data = _post()
    if "message" in data:   # native /api/chat shape
        text = data["message"].get("content", "")
        if not text:
            raise RuntimeError("ollama returned empty content: "
                               + str(data)[:200])
    else:                   # OpenAI-compat shape
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("ollama returned no choices: "
                               + str(data)[:200])
        text = choices[0]["message"]["content"]
    plan = _validate_plan(_extract_json(text))
    # local models: usage is informational (cost ~$0); record it so the
    # suite can compare token counts against the cloud backends
    last_usage.update(data.get("usage") or {})
    return plan



def plan_hw(query: str, summary: str | None,
            harness: str = "omp", timeout: int = 300,
            hw_bin: str | None = None) -> dict:
    """Plan via the hw harness wrapper: route the planner prompt through
    one of the installed coding-agent harnesses (omp/prime/grok/hermes)
    headlessly, then apply the same schema validation as other backends.

    The orchestrator keeps its tricks (grammar gate, governance, kernel
    state) — hw only replaces *who plans*.  Execution stays local and
    deterministic.
    """
    import shutil as _shutil
    import subprocess as _sp
    if not hw_bin:
        hw_bin = os.environ.get("HW_BIN") or _shutil.which("hw")
    if not hw_bin:
        _fallback = Path(__file__).resolve().parent.parent / "02-tools" / "hw" / "hw"
        if _fallback.exists():
            hw_bin = str(_fallback)
    if not hw_bin:
        raise RuntimeError("hw not found (install via "
                           "~/Projects/internal.source/02-tools/hw/install.sh)")
    if harness not in ("omp", "prime", "grok", "hermes", "jcode", "claude", "codex", "qwen", "kimi", "gemini"):
        raise RuntimeError(f"unknown harness '{harness}' for hw backend")
    prompt = (_planner_system(summary)
              + "\n\nUSER QUERY:\n" + query)
    argv = [hw_bin, "run", harness, prompt, "--timeout", str(timeout)]
    try:
        p = _sp.run(argv, capture_output=True, text=True, timeout=timeout + 30)
    except Exception as exc:
        raise RuntimeError(f"hw run {harness} failed to start ({exc})") from exc
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        raise RuntimeError(f"hw run {harness} failed (rc={p.returncode}): "
                           + (tail[-1] if tail else "no output"))
    text = p.stdout or ""
    if not text.strip():
        raise RuntimeError(f"hw run {harness} returned empty output")
    plan = _validate_plan(_extract_json(text))
    last_usage.update({"harness": harness, "model": harness,
                       "timeout_s": timeout})
    return plan


def _validate_plan(plan: dict) -> dict:
    ops = []
    for raw in plan.get("ops", []):
        t = str(raw.get("type", "")).upper()
        if t not in VALID_TYPES:
            continue
        op = {"type": t}
        for key in ("path", "content", "pattern", "target", "command"):
            val = raw.get(key)
            if not val:
                continue
            # LLMs sometimes emit EXECUTE command as an argv list
            # (e.g. ["wc","-l","a.c"]) — join it, never str() the repr,
            # otherwise the kernel allowlist sees "['wc'," and rejects it.
            if key == "command" and isinstance(val, list):
                op[key] = " ".join(str(t) for t in val)
            else:
                op[key] = str(val)
        for key in ("line_start", "line_end", "max_results"):
            if raw.get(key) is not None:
                op[key] = int(raw[key])
        ops.append(op)
    if not ops:
        raise ValueError("planner returned no valid ops")
    strategy = str(plan.get("strategy", "DELTA")).upper()
    strategy = strategy if strategy in ("DELTA", "TARGETED", "FULL") else "DELTA"
    out = {"ops": ops, "strategy": strategy}
    if plan.get("target_paths"):
        out["target_paths"] = [str(p) for p in plan["target_paths"]]
    return out


def plan_mesh(query: str, summary: str | None, mesh, timeout: float = 20.0,
             prefer: list[str] | None = None) -> dict:
    timeout = float(os.environ.get("SP_MESH_TIMEOUT", timeout))
    """Inference-broker path: ask the mesh who can plan this task.  The
    local router's model pick is passed as a PREFERENCE so the broker
    can score providers by capability (model match, load, freshness)."""
    plan = mesh.ask(query, summary, timeout=timeout,
                    prefer_models=prefer or None)
    return normalize_plan(plan)


def make_plan(query: str, summary: str | None, backend: str = "mock",
              model: str | None = None, mesh=None,
              mesh_timeout: float = 20.0, mesh_prefer: list[str] | None = None,
              grammar: str = "lenient") -> dict:
    """Plan with the chosen backend, then apply the grammar gate.

    grammar="lenient" (default): best-effort -- malformed ops are
    dropped, the plan still runs (historical behavior).
    grammar="strict": reject the WHOLE plan with actionable errors when
    anything violates the plan grammar (unknown op type, missing
    required field, bad strategy, wrong value type).  Use with
    --plan-grammar strict on the runners; the orchestrator treats a
    ValueError as a plan failure and falls back (oracle-retry path)."""
    if backend == "mesh":
        if mesh is None:
            raise RuntimeError("backend=mesh requires a mesh node (--mesh-name)")
        try:
            plan = plan_mesh(query, summary, mesh, timeout=mesh_timeout,
                             prefer=mesh_prefer)
        except Exception as exc:
            raise RuntimeError(f"mesh planner failed ({exc})") from exc
    elif backend == "ollama":
        try:
            plan = plan_ollama(query, summary, model=model or "qwen2.5-coder:7b")
        except Exception as exc:  # server down, model missing, bad JSON
            raise RuntimeError(f"ollama planner failed ({exc}); start `ollama serve` "
                               "and pull the model, or use --backend mock") from exc
    elif backend in OPENAI_BACKEND_NAMES:
        try:
            canon = resolve_openai_backend(backend)
            plan = normalize_plan(
                plan_openai_backend(canon, query, summary, model=model))
        except Exception as exc:
            try:
                hint = _OPENAI_PRESETS[resolve_openai_backend(backend)].get(
                    "hint", "check base URL / API key")
            except KeyError:
                hint = "check base URL / API key"
            raise RuntimeError(
                f"{backend} planner failed ({exc}); {hint} "
                "or use --backend mock"
            ) from exc
    elif backend in ("hw", "harness"):
        # harness wrapper backend: plan through omp/prime/grok/hermes.
        # `model` selects the harness (default omp when unset/invalid).
        try:
            _hsel = model if model in ("omp", "prime", "grok", "hermes") else "omp"
            plan = normalize_plan(plan_hw(query, summary, harness=_hsel))
        except Exception as exc:
            raise RuntimeError(f"hw planner failed ({exc})") from exc
    elif backend in ("deepseek", "normal"):
        # "normal" = naive full-context baseline (same model, whole repo);
        # "deepseek" = StatePod containment (state summary only)
        try:
            plan = normalize_plan(plan_deepseek(query, summary, model=model,
                                                full=(backend == "normal")))
        except Exception as exc:
            raise RuntimeError(f"deepseek planner failed ({exc})") from exc
    else:
        plan = normalize_plan(plan_mock(query, summary))
    if grammar == "strict":
        from plan_grammar import validate
        ok, errors = validate(plan)
        if not ok:
            raise ValueError("plan grammar violation: "
                             + "; ".join(errors))
    # DAG validation (optional: only when ops have id/depends)
    ops = plan.get("ops") or []
    if any(op.get("id") or op.get("depends") for op in ops):
        ok, derrs = validate_dag(ops)
        if not ok:
            msg = "plan dag: " + "; ".join(derrs)
            if grammar == "strict":
                raise ValueError(msg)
            import sys as _sys
            print(f"[planner] WARN {msg}", file=_sys.stderr)
    return plan
