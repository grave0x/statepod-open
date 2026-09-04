"""frontierharness.py — run FrontierHarness Eval tasks through sw.

FrontierHarness Eval (github.com/runta-dev/frontier-harness, v1, Sep 2026)
ships 30 task definitions (21 terminal-bench + 9 deep-swe/datacurve):
instruction.md + task.toml per task.  Verifiers, environments (Docker
images) and solutions are intentionally NOT public.

This handler is a self-contained, stdlib-only eval runner (sibling of
research.py) that:

  * sync  — downloads the benchmark task repo (or copies a local tree),
            so evals can be selected by PACK (terminal-bench / deep-swe)
            or INDIVIDUAL task name;
  * list  — shows the cached task inventory (pack, difficulty, docker
            image, agent timeout);
  * run   — executes each selected task through sw's own one-shot path
            (harness/main.py) in an ISOLATED seeded scratch repo per
            task, with a hard per-task timeout, then classifies the
            outcome honestly:

              DENY    the kernel/governance gate refused an action the
                      task needs (EXECUTE whitelist block, unsafe path,
                      governance denial) — captured with the exact tool
                      or reason;
              FAIL    ran without a gate denial but did not positively
                      verify — the default, because FHE verifiers are
                      private: without them nothing can be PASSed, so a
                      "kernel-clean" run is reported as fail-unverified
                      with the artifacts it produced;
              TIMEOUT hit the per-task deadline;
              ERROR   runner/infra failure.

    PASS is deliberately unreachable in this build (no verifier, no
    oracle).  A swarmstate run therefore measures the *structural
    denial surface* and kernel divergence against the benchmark — the
    citable harness-design finding — not model quality.

Usage:
  python3 harness/frontierharness.py sync   [--source URL|PATH] [--force] [--update]
  python3 harness/frontierharness.py list   [--pack terminal-bench|deep-swe] [NAME ...]
  python3 harness/frontierharness.py run    [--pack ...] [NAME ...] [--limit N]
                                            [--backend mock|deepseek|hw|...] [--model M]
                                            [--timeout S] [--results DIR] [--work DIR]
                                            [--dollar-per-1k RATE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

VERSION = "0.1.0"

REPO = Path(__file__).resolve().parent.parent
FHE_URL = "https://github.com/runta-dev/frontier-harness"
FHE_REF = "main"

# cache root for the downloaded benchmark repo
def _default_cache() -> Path:
    return Path(os.environ.get("SW_FHE_ROOT")
                or Path.home() / ".swarmstate" / "fhe")

# pack labelling follows benchmark.json task_sources:
#   terminal_bench -> terminal-bench/terminal-bench-2-1 (21 tasks)
#   deep_swe       -> datacurve-ai/deep-swe v1.1          (9 tasks)
PREFIX_PACK = {
    "terminal-bench": "terminal-bench",
    "datacurve": "deep-swe",
}
PACK_ALIASES = {
    "terminal-bench": "terminal-bench", "terminal_bench": "terminal-bench",
    "tb": "terminal-bench", "terminal": "terminal-bench",
    "deep-swe": "deep-swe", "deep_swe": "deep-swe", "ds": "deep-swe",
    "deep": "deep-swe", "datacurve": "deep-swe",
}

def _pack_of(task_name: str) -> str:
    prefix = task_name.split("/", 1)[0]
    return PREFIX_PACK.get(prefix, "other")

# ------------------------------------------------------------- tasks
def load_tasks(repo_dir: Path) -> list[dict]:
    """Scan repo_dir/tasks/*/task.toml -> task dicts (sorted by key)."""
    tasks = []
    td = Path(repo_dir) / "tasks"
    if not td.is_dir():
        raise FileNotFoundError(f"no tasks/ dir under {repo_dir}")
    for key in sorted(p.name for p in td.iterdir()
                      if (p / "task.toml").is_file()):
        toml = td / key / "task.toml"
        d = {}
        if tomllib:
            with open(toml, "rb") as f:
                d = tomllib.load(f)
        t = d.get("task", {})
        md = d.get("metadata", {})
        env = d.get("environment", {})
        ver = d.get("verifier", {})
        name = t.get("name", key)
        tasks.append({
            "key": key,
            "name": name,
            "pack": _pack_of(name),
            "difficulty": md.get("difficulty", "") or "",
            "category": md.get("category", "") or "",
            "docker_image": env.get("docker_image", ""),
            "agent_timeout": (d.get("agent") or {}).get("timeout_sec")
            or t.get("timeout_sec") or env.get("timeout_sec"),
            "verifier_timeout": ver.get("timeout_sec"),
            "workdir": env.get("workdir", "/app"),
            "allow_internet": bool(env.get("allow_internet")),
            "instruction_path": str(td / key / "instruction.md"),
        })
    return tasks

def pack_counts(repo_dir: Path) -> dict[str, int]:
    c: dict[str, int] = {}
    for t in load_tasks(repo_dir):
        c[t["pack"]] = c.get(t["pack"], 0) + 1
    return c

def _instruction(task: dict) -> str:
    p = Path(task["instruction_path"])
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

def select(tasks: list[dict], pack: str | None = None,
           names: list[str] | None = None, limit: int | None = None
           ) -> list[dict]:
    """Filter tasks by pack (canonical or alias) and/or name substrings."""
    out = list(tasks)
    if pack:
        canon = PACK_ALIASES.get(pack.strip().lower(), pack.strip().lower())
        out = [t for t in out if t["pack"] == canon]
    if names:
        want = [n.strip().lower() for n in names if n.strip()]
        out = [t for t in out
               if any(w in t["key"].lower() or w in t["name"].lower()
                      for w in want)]
    if limit:
        out = out[:limit]
    return out

# --------------------------------------------------------------- sync
def sync(source: str | Path | None = None, cache: Path | None = None,
         force: bool = False, update: bool = False) -> tuple[Path, str]:
    """Download (git clone, depth 1) or copy the benchmark task repo.

    source=None -> FHE_URL; a Path/URL string is used as-is (local
    trees are copied so offline tests are hermetic).  Returns
    (repo_dir, human report)."""
    cache = cache or _default_cache()
    # canonical repo dir for URL and local sources alike, so list/run
    # find whatever sync() stored
    dst = cache / "frontier-harness"
    if source is None:
        if force and dst.exists():
            shutil.rmtree(dst)
        elif update and (dst / ".git").exists():
            r = subprocess.run(["git", "-C", str(dst), "pull",
                                "--ff-only", "-q"],
                               capture_output=True, text=True)
            note = "updated" if r.returncode == 0 else \
                f"pull failed rc={r.returncode}: {(r.stderr or r.stdout).strip()[:200]}"
            n = len(load_tasks(dst)) if dst.exists() else 0
            return dst, f"{note}; {n} tasks"
        if not (dst / "tasks").is_dir():
            dst.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", FHE_REF,
                 "--", FHE_URL, str(dst)],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(
                    f"clone failed rc={r.returncode}: "
                    f"{(r.stderr or r.stdout).strip()[:300]}")
    else:
        if isinstance(source, str) and re.match(r"https?://", source):
            # explicit git URL: clone like the default source
            if dst.exists() and not force:
                pass  # canonical dir already present
            else:
                if dst.exists():
                    shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", FHE_REF,
                     "--", source, str(dst)],
                    capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(
                        f"clone failed rc={r.returncode}: "
                        f"{(r.stderr or r.stdout).strip()[:300]}")
        else:
            src = Path(source).resolve()
            if dst.exists() and dst.resolve() == src:
                pass  # already the canonical dir
            elif not dst.exists() or force:
                if dst.exists():
                    shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))
    if not (dst / "tasks").is_dir():
        raise RuntimeError(f"source has no tasks/ dir: {dst}")
    counts = pack_counts(dst)
    parts = [f"frontier-harness tasks -> {dst}",
             f"{sum(counts.values())} tasks: "
             + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    return dst, "\n".join(parts)

# ---------------------------------------------------------- classify
DENY_PATTERNS = [
    (re.compile(r"ERROR: command not whitelisted: (\S+)"),
     lambda m: f"kernel EXECUTE blocked '{m.group(1)}' (not whitelisted)"),
    (re.compile(r"ERROR: argument not allowed[^\n]*"),
     lambda m: "kernel EXECUTE rejected arguments (shell meta / mutating flag)"),
    (re.compile(r"ERROR: invalid or unsafe (path|target)"),
     lambda m: "kernel rejected an unsafe path (outside work root)"),
    (re.compile(r"ERROR: service management disabled"),
     lambda m: "kernel service-management gate disabled"),
    (re.compile(r"governance: plan denied[^\n]*"),
     lambda m: "governance gate denied the plan"),
    (re.compile(r"HUMAN_DENY"),
     lambda m: "governance HUMAN_DENY"),
]
FAIL_PATTERNS = [
    (re.compile(r"escalation planner failed"),
     lambda m: "escalation planner failed"),
    (re.compile(r"planner failed[^\n]*"),
     lambda m: "planner failed"),
    (re.compile(r"plan rejected[^\n]*"),
     lambda m: "plan rejected (grammar gate)"),
    (re.compile(r"!! op [^\n]*"),
     lambda m: "kernel op execution error"),
    (re.compile(r"escalation needed"),
     lambda m: "escalation needed (WEAK)"),
]

def classify(text: str) -> tuple[str, str]:
    """Map a sw one-shot transcript to (outcome, reason)."""
    if not text:
        return "ERROR", "no output captured"
    low = text.lower()
    for rx, fn in DENY_PATTERNS:
        m = rx.search(text)
        if m:
            return "DENY", fn(m)
    if "status: deny" in low:
        return "DENY", "sw status DENY"
    for rx, fn in FAIL_PATTERNS:
        m = rx.search(text)
        if m:
            return "FAIL", fn(m)
    if "status: weak" in low or "status: fail" in low:
        return "FAIL", "sw status " + ("weak" if "status: weak" in low
                                       else "fail")
    return "FAIL", "kernel ran clean but outcome unverified locally (FHE verifier is private)"

# -------------------------------------------------------- run one task
def _seed_repo(scratch: Path) -> None:
    subprocess.run(["git", "-C", str(scratch), "init", "-q"],
                   check=False, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "config", "user.email",
                    "sw-eval@swarmstate"], capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "config", "user.name",
                    "sw eval"], capture_output=True)
    (scratch / ".gitignore").write_text(".swarmstate/\n__pycache__/\n")
    subprocess.run(["git", "-C", str(scratch), "add", "-A"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "commit", "-qm", "seed"],
                   capture_output=True)

def _artifacts(scratch: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(scratch), "status",
                        "--porcelain", "-z"],
                       capture_output=True, text=True)
    out = []
    for entry in r.stdout.split("\0"):
        if not entry or len(entry) < 4:
            continue
        path = entry[3:]
        if path == ".swarmstate/" or path.startswith(".swarmstate/"):
            continue
        # porcelain -z quotes paths with special chars: strip one layer
        if len(path) >= 2 and path[0] == path[-1] == '"':
            path = path[1:-1]
        out.append(path)
    return out

TOKEN_RX = re.compile(
    r'"(?:prompt|completion|total)_tokens"\s*:\s*(\d+)'
    r'|(?:prompt|completion|total)_tokens\s*=\s*(\d+)', re.I)

def _cost_of(text: str, per_1k: float) -> float:
    """Dollar cost from token fields in a transcript.

    ``total_tokens`` already includes prompt+completion, so when any
    total field is present we sum only totals (one per call); otherwise
    we sum the prompt/completion parts.  Mixing both would double-count.
    """
    if per_1k <= 0:
        return 0.0
    totals: list[int] = []
    parts = 0
    for m in TOKEN_RX.finditer(text):
        v = int(m.group(1) or m.group(2))
        if "total_tokens" in m.group(0):
            totals.append(v)
        else:
            parts += v
    toks = sum(totals) if totals else parts
    return round(toks / 1000.0 * per_1k, 4) if toks else 0.0

def run_one(task: dict, backend: str, model: str | None,
            timeout: float, work: Path, rate: float = 0.0
            ) -> dict:
    """Run one task through sw's one-shot path in a fresh scratch repo."""
    key = task["key"]
    scratch = Path(tempfile.mkdtemp(prefix="sw-eval-", dir=str(work)))
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "task": key, "pack": task["pack"],
           "backend": backend, "model": model}
    t0 = time.time()
    out, rc, outcome, reason = "", -99, "ERROR", ""
    try:
        _seed_repo(scratch)
        env = dict(os.environ)
        lib = REPO / "libswarmstate.so"
        if lib.exists():
            env.setdefault("SWARMSTATE_LIB", str(lib))
        argv = [sys.executable, str(REPO / "harness" / "main.py"),
                str(scratch), "--backend", backend, "--query",
                _instruction(task)]
        if model:
            argv[2:2] = ["--model", model]  # keep positional root first
        p = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             env=env, cwd=str(REPO))
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            outcome, reason = "TIMEOUT", f"exceeded {timeout:.0f}s cap"
        else:
            rc = p.returncode
            if rc != 0 and not out.strip():
                outcome, reason = "ERROR", f"runner rc={rc} (no output)"
            else:
                outcome, reason = classify(out)
                if rc != 0:
                    reason += f" (rc={rc})"
    except Exception as exc:  # noqa: BLE001 — keep the suite going
        outcome, reason = "ERROR", f"{type(exc).__name__}: {exc}"
    dt = time.time() - t0
    row.update({"outcome": outcome, "reason": reason,
                "seconds": round(dt, 3), "rc": rc,
                "cost": _cost_of(out, rate),
                "artifacts": _artifacts(scratch)})
    log = work / "logs" / f"{key.replace('/', '__')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(out[-60000:])
    shutil.rmtree(scratch, ignore_errors=True)
    return row

# ---------------------------------------------------------------- run
def run(selected: list[dict], backend: str = "mock", model: str | None = None,
        timeout: float = 300.0, work: Path | None = None,
        results: Path | None = None, rate: float = 0.0,
        progress: bool = True) -> tuple[Path, list[dict]]:
    work = work or Path(tempfile.mkdtemp(prefix="sw-eval-work-"))
    work.mkdir(parents=True, exist_ok=True)
    results = results or (Path.cwd() / "eval" /
                          time.strftime("%Y%m%d-%H%M%S"))
    results.mkdir(parents=True, exist_ok=True)
    try:
        if not (results / "logs").exists():
            (results / "logs").symlink_to(work / "logs",
                                          target_is_directory=True)
    except OSError:
        pass
    jl = results / "results.jsonl"
    rows: list[dict] = []
    for i, task in enumerate(selected, 1):
        if progress:
            print(f"[{i}/{len(selected)}] {task['key']} "
                  f"(pack={task['pack']}, backend={backend})…",
                  flush=True)
        row = run_one(task, backend, model, timeout, work, rate)
        rows.append(row)
        with open(jl, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        if progress:
            print(f"    -> {row['outcome']} in {row['seconds']}s "
                  f"[{row['reason'][:110]}]")
    write_report(rows, results, repo_dir=None, backend=backend,
                 timeout=timeout)
    return results, rows

# ------------------------------------------------------------- report
def write_report(rows: list[dict], results: Path,
                 repo_dir: Path | None, backend: str,
                 timeout: float) -> Path:
    n = len(rows)
    cnt = {"DENY": 0, "FAIL": 0, "TIMEOUT": 0, "ERROR": 0, "PASS": 0}
    for r in rows:
        cnt[r["outcome"]] = cnt.get(r["outcome"], 0) + 1
    times = sorted(r["seconds"] for r in rows)
    med = times[len(times) // 2] if times else 0.0
    total = round(sum(times), 1)
    packs = sorted({r["pack"] for r in rows})
    lines = [
        "# FrontierHarness Eval — swarmstate run",
        "",
        f"> benchmark: {FHE_URL} · tasks run: {n} · "
        f"backend: `{backend}` · per-task cap: {timeout:.0f}s · "
        f"run: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "",
        "**Honest caveats.** FHE ships task definitions only — verifiers, "
        "Docker envs and solutions are private.  Every task ran in a plain "
        "seeded git scratch repo through sw's real one-shot path "
        "(`harness/main.py`); no task environment was emulated.  PASS is "
        "therefore unreachable: a green kernel run is reported FAIL "
        "(unverified locally).  DENY records an environment gate that "
        "refused an action the task needs — the structural finding, "
        "not a model-quality claim.",
        "",
        "| task | pack | outcome | time_s | cost | artifacts | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        arts = ", ".join(r["artifacts"][:4]) or "—"
        lines.append(
            f"| {r['task']} | {r['pack']} | {r['outcome']} | "
            f"{r['seconds']} | {r['cost']:.4f} | {arts} | "
            f"{r['reason'][:100].replace('|', '/')} |")
    lines += [
        "",
        f"**Aggregate:** DENY {cnt['DENY']} · FAIL {cnt['FAIL']} · "
        f"TIMEOUT {cnt['TIMEOUT']} · ERROR {cnt['ERROR']} · "
        f"PASS {cnt['PASS']} · median {med}s · wall {total}s.",
        "**Packs:** " + ", ".join(packs) + ".",
        "",
        "Pass rate is not reportable until a verifier/oracle is attached; "
        "see `harness/frontierharness.py` header for the denial taxonomy.",
    ]
    md = results / "report.md"
    md.write_text("\n".join(lines))
    return md

# --------------------------------------------------------------- cli
def _fmt_task(t: dict) -> str:
    return (f"{t['key']:<42} {t['pack']:<14} "
            f"{t['difficulty'] or '-':<10} {t['category'] or '-':<18} "
            f"{(t['docker_image'] or '-')[:38]}")

def cli_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="frontierharness",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="download/copy the benchmark task repo")
    s.add_argument("--source", default=None,
                   help="git URL or local path (default runta-dev "
                        "frontier-harness)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--update", action="store_true")

    l = sub.add_parser("list", help="list cached tasks")
    l.add_argument("--pack", default=None,
                   help="terminal-bench | deep-swe")
    l.add_argument("names", nargs="*", help="substring filters")
    l.add_argument("--json", action="store_true")

    r = sub.add_parser("run", help="run selected tasks through sw")
    r.add_argument("--pack", default=None)
    r.add_argument("names", nargs="*")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--backend", default="mock")
    r.add_argument("--model", default=None)
    r.add_argument("--timeout", type=float, default=300.0)
    r.add_argument("--results", default=None, help="results dir (default ./eval/<ts>)")
    r.add_argument("--work", default=None, help="scratch work dir")
    r.add_argument("--dollar-per-1k", type=float, default=0.0,
                   help="LLM cost rate for token-bearing backends")
    r.add_argument("--no-sync", action="store_true",
                   help="require an existing cache (no auto-download)")

    args = ap.parse_args(argv)
    cache = _default_cache()
    try:
        if args.cmd == "sync":
            repo, note = sync(args.source, cache=cache,
                              force=args.force, update=args.update)
            print(note)
            return 0
        # list / run need the task repo (auto-download on first use)
        repo = cache / "frontier-harness"
        if not (repo / "tasks").is_dir():
            if args.cmd == "run" and getattr(args, "no_sync", False):
                print(f"no cached benchmark (run `frontierharness sync` "
                      f"or drop --no-sync): {repo}")
                return 1
            if args.cmd == "list":
                repo, _ = sync(None, cache=cache)
            else:
                repo, note = sync(None, cache=cache)
                print(note)
        tasks = load_tasks(repo)
        if args.cmd == "list":
            sel = select(tasks, pack=args.pack, names=args.names)
            if not sel:
                print("no tasks match (run `sync` first? check --pack "
                      "terminal-bench|deep-swe)")
                return 1
            if args.json:
                print(json.dumps(sel, default=str, indent=1))
            else:
                pc = pack_counts(repo)
                inv = ", ".join(f"{k} {v}" for k, v in sorted(pc.items()))
                print(f"{len(sel)} task(s) ({inv} cached)")
                print(_fmt_task({"key": "task", "pack": "pack",
                                 "difficulty": "difficulty",
                                 "category": "category",
                                 "docker_image": "docker_image"}))
                for t in sel:
                    print(_fmt_task(t))
            return 0
        sel = select(tasks, pack=args.pack, names=args.names,
                     limit=args.limit)
        if not sel:
            print("no tasks match the selection")
            return 1
        results, rows = run(sel, backend=args.backend, model=args.model,
                            timeout=args.timeout,
                            work=Path(args.work) if args.work else None,
                            results=Path(args.results) if args.results
                            else None,
                            rate=args.dollar_per_1k)
        print(f"\nresults -> {results}/report.md "
              f"({len(rows)} runs, "
              + ", ".join(f"{k} {v}" for k, v in
                          _counts(rows).items()) + ")")
        return 0
    except (RuntimeError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}")
        return 1

def _counts(rows: list[dict]) -> dict[str, int]:
    c: dict[str, int] = {}
    for r in rows:
        c[r["outcome"]] = c.get(r["outcome"], 0) + 1
    return c

if __name__ == "__main__":
    sys.exit(cli_main())
