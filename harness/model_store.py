"""Shared model store for SwarmState — auto-discovery and auto-launch.

A shared model folder lives at swarmstate/models/ (symlinked to ~/.ollama/models
by default; `sw model-store link` to change it).

When `sw` needs a local model it:
  1. Checks if the requested model is in the store.
  2. If ollama serve is not running, starts it automatically.
  3. If the model is not pulled, pulls it automatically.
  4. Returns the ready base URL for the planner.

Usage (from swarmcli / orchestrator):
    from model_store import ensure_ollama_running, ensure_model, status, default_model

    ensure_ollama_running(shared_store="~/Projects/internal.source/02-tools/swarmstate/models")
    ensure_model("qwen2.5-coder:1.5b", shared_store="...")

CLI:
    sw model-store status    -- list available models
    sw model-store link     -- create/update the models/ symlink
    sw model-store pull M   -- pull a model
    sw model-store doctor   -- check ollama + model availability
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Default shared model store: swarmstate/models/ (symlink to ~/.ollama/models)
REPO = Path(__file__).resolve().parent.parent
DEFAULT_STORE = REPO / "models"
OLLAMA_PORT = 11434
OLLAMA_BASE = f"http://localhost:{OLLAMA_PORT}"
_OLLAMA_SERVE_PID: int | None = None  # cached pid of our spawned ollama serve


# -------------------------------------------------------------------------- #
# Low-level checks
# -------------------------------------------------------------------------- #

def _check_ollama_serve() -> bool:
    """Return True if we can reach ollama serve."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "2", f"{OLLAMA_BASE}/api/tags"],
            capture_output=True, timeout=5,
            env={**os.environ, "PATH": os.environ.get("PATH", "")}
        )
        return r.returncode == 0
    except Exception:
        return False


def _ollama_pid() -> int | None:
    """Return PID of ollama serve if running, else None."""
    try:
        r = subprocess.run(
            ["pgrep", "-x", "ollama"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def _is_our_ollama(pid: int) -> bool:
    """Heuristic: our spawned ollama serve listens on OLLAMA_PORT in its args."""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=3
        )
        return str(OLLAMA_PORT) in r.stdout
    except Exception:
        return False


# -------------------------------------------------------------------------- #
# Core API
# -------------------------------------------------------------------------- #

def ensure_ollama_running(shared_store: Path | str | None = None) -> str:
    """Start ollama serve if not already running.
    Sets OLLAMA_MODELS so ollama uses the shared store.
    Returns the base URL of the running server."""
    # global _OLLAMA_SERVE_PID  # refactored: use class attribute

    if _check_ollama_serve():
        return OLLAMA_BASE  # already running

    shared_store = Path(shared_store) if shared_store else DEFAULT_STORE
    store_path = str(shared_store.resolve())

    print(f"[model-store] ollama serve not running — starting on :{OLLAMA_PORT}")
    env = {
        **os.environ,
        "OLLAMA_MODELS": store_path,
        "OLLAMA_HOST":   f"127.0.0.1:{OLLAMA_PORT}",
    }

    # Start in background, redirect stdout/stderr to a log
    log_path = REPO / ".swarmstate" / "ollama-serve.log"
    log_path.parent.mkdir(exist_ok=True)
    log_file = open(log_path, "ab")
    pid_file = REPO / ".swarmstate" / "ollama-serve.pid"

    proc = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        stdout=log_file.fileno(),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _OLLAMA_SERVE_PID = proc.pid
    pid_file.write_text(str(proc.pid))
    print(f"[model-store] ollama serve started (PID={proc.pid}), log -> {log_path}")

    # Wait for it to be ready (up to 15s)
    for i in range(30):
        time.sleep(0.5)
        if _check_ollama_serve():
            print(f"[model-store] ollama serve ready after {i*0.5:.1f}s")
            return OLLAMA_BASE

    raise RuntimeError(
        f"ollama serve failed to start after 15s — check {log_path}"
    )


def available_models(shared_store: Path | str | None = None) -> list[dict]:
    """Return the list of models available in the store (via ollama list)."""
    ensure_ollama_running(shared_store)
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "5", f"{OLLAMA_BASE}/api/tags"],
            capture_output=True, text=True, timeout=8
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        return data.get("models", [])
    except Exception:
        return []


def ensure_model(name: str, shared_store: Path | str | None = None) -> None:
    """Pull `name` if it is not already in the store.  Uses OLLAMA_MODELS so
    the model lands in the shared store directory."""
    available = {m["name"] for m in available_models(shared_store)}
    if name in available:
        print(f"[model-store] {name} already in store — no pull needed")
        return

    print(f"[model-store] pulling {name} ...")
    ensure_ollama_running(shared_store)

    shared_store = Path(shared_store) if shared_store else DEFAULT_STORE
    env = {
        **os.environ,
        "OLLAMA_MODELS": str(shared_store.resolve()),
        "OLLAMA_HOST":   f"127.0.0.1:{OLLAMA_PORT}",
    }
    # Pull with visible output (tee to log)
    log_path = REPO / ".swarmstate" / "ollama-pull.log"
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(
            ["ollama", "pull", name],
            env=env,
            stdout=lf.fileno(),
            stderr=subprocess.STDOUT,
        )
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ollama pull {name} failed — see {log_path}")
    print(f"[model-store] {name} pulled successfully")


def default_model(shared_store: Path | str | None = None) -> str | None:
    """Return the best available model from the store.
    Prefers qwen2.5-coder:3b > qwen2.5-coder:1.5b > first available."""
    ensure_ollama_running(shared_store)
    available = available_models(shared_store)
    names = [m["name"] for m in available]
    prefs = ["qwen2.5-coder:3b", "qwen2.5-coder:1.5b", "qwen2.5-coder:7b",
             "qwen2.5-coder:3b-instruct"]
    for pref in prefs:
        if pref in names:
            return pref
    return names[0] if names else None


def status(shared_store: Path | str | None = None) -> dict:
    """Return a dict describing the current model store state."""
    store = Path(shared_store) if shared_store else DEFAULT_STORE
    serve_running = _check_ollama_serve()
    pid = _ollama_pid()
    our_pid = pid if pid and _is_our_ollama(pid) else None
    models = available_models(shared_store) if serve_running else []

    return {
        "store_path": str(store.resolve()),
        "store_exists": store.is_dir(),
        "ollama_serve_running": serve_running,
        "ollama_serve_pid": pid,
        "our_spawned_pid": our_pid,
        "models": [m["name"] for m in models],
        "default_model": default_model(shared_store) if serve_running else None,
    }


# -------------------------------------------------------------------------- #
# CLI (sw model-store …)
# -------------------------------------------------------------------------- #

def cmd_status(args) -> int:
    s = status()
    print("Model store status")
    print(f"  store path : {s['store_path']}")
    print(f"  store exists: {s['store_exists']}")
    print(f"  ollama serve : {'running' if s['ollama_serve_running'] else 'NOT running'} "
          + (f"(PID={s['ollama_serve_pid']})" if s['ollama_serve_pid'] else ""))
    if s['our_spawned_pid']:
        print(f"  our spawn PID: {s['our_spawned_pid']}")
    if s['models']:
        print(f"  available models ({len(s['models'])}):")
        for m in s['models']:
            print(f"    - {m}")
    else:
        print("  available models: (none — pull one with `sw model-store pull`)")
    if s['default_model']:
        print(f"  default model: {s['default_model']}")
    return 0


def cmd_pull(args) -> int:
    name = args.model
    if not name:
        print("Error: specify a model name, e.g. `sw model-store pull qwen2.5-coder:1.5b`")
        return 1
    try:
        ensure_model(name)
        print(f"Done: {name} is ready")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_doctor(args) -> int:
    """Quick health check: can we reach ollama? are models available?"""
    s = status()
    ok = s["ollama_serve_running"] and bool(s["models"])
    mark = "ok" if ok else "WARN"
    print(f"[{mark}] model-store  ollama={s['ollama_serve_running']}  "
          f"models={len(s['models'])}  default={s['default_model'] or 'none'}")
    if not s["ollama_serve_running"]:
        print("  → start with `sw model-store start` or let `sw` auto-start on first use")
    if not s["models"]:
        print("  → pull a model: `sw model-store pull qwen2.5-coder:1.5b`")
    return 0 if ok else 1


def cmd_start(args) -> int:
    """Explicitly start ollama serve."""
    try:
        url = ensure_ollama_running()
        print(f"ollama serve running at {url}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SwarmState model store CLI")
    ap.add_argument("--store", default=None, help="model store path (default: swarmstate/models)")
    sp = ap.add_subparsers(dest="cmd")

    p_status = sp.add_parser("status", help="show store status")
    p_start  = sp.add_parser("start",  help="start ollama serve")
    p_pull   = sp.add_parser("pull",   help="pull a model into the store")
    p_pull.add_argument("model",        help="model name (e.g. qwen2.5-coder:1.5b)")
    p_doc    = sp.add_parser("doctor",  help="health check")
    p_doc.add_argument("--smoke", action="store_true")

    args = ap.parse_args()
    if args.cmd == "status":
        sys.exit(cmd_status(args))
    elif args.cmd == "start":
        sys.exit(cmd_start(args))
    elif args.cmd == "pull":
        sys.exit(cmd_pull(args))
    elif args.cmd == "doctor":
        sys.exit(cmd_doctor(args))
    else:
        # Default: status
        sys.exit(cmd_status(args))
