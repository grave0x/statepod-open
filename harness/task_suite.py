"""Task suite: run representative agent tasks end-to-end and measure the
context-containment economics (strategy, context bytes, op counts).

Usage:  python3 harness/task_suite.py [--backend mock|deepseek] [--tasks N]
                                    [--results PATH]
The suite builds a scratch repo, then runs each task through the
orchestrator loop. For every task it reports the context strategy,
how many ops ran, how many bytes the kernel returned (what the LLM
would have seen), and success.

Timing & resources: each task records started/finished wall-clock time,
duration_ms, and a kernel resource snapshot (mem/load) at start and end.
Every run appends one JSONL line per task to --results (default
~/.local/state/swarmstate/task_suite.jsonl) so mock vs. real-LLM plans
can be compared over time (success AND speed).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

from orchestrator import Orchestrator  # noqa: E402
from planner import OPENAI_BACKEND_NAMES  # noqa: E402
from swarmstate import KERNEL_VERSION  # noqa: E402

import corpus  # noqa: E402  (families, FIXTURE, ORACLES)
import gpu  # noqa: E402  (GPU detection for local-model runs)

TASKS = [
    ("rename variable", 'rename "x" to "count" in src/math.c'),
    ("add function", 'add a function "double avg(double a, double b)" to src/math.c'),
    ("fix lint error", "find unused variables in src/math.c"),
    ("explain code", "explain what src/math.c does"),
    ("list functions", "list the functions in src/math.c"),
    ("find TODO", "grep 'TODO'"),
    ("repo status", "status"),
    ("diff a file", "diff src/math.c"),
    ("count lines", "count lines of src/math.c"),
    ("write note", 'write "notes.md" "meeting notes\n"'),
]


def _semantic_ok(task: str, root: Path, out: str) -> tuple[bool, str]:
    """Expected-outcome check on the scratch repo AFTER the task ran.

    Returns (ok, reason). Mechanical ok (no kernel error) is not
    enough: 'fix lint error' can pass by merely reading the file, and
    'rename variable' can write garbage to a new path. These checks
    verify the state the task was supposed to produce. The reason feeds
    the oracle-in-the-loop retry rung, so on failure it states the
    target state in model-actionable terms. Read-only tasks -> (True,
    "") (no state to verify)."""
    if task in corpus.ORACLES:
        return corpus.ORACLES[task](root, out)
    src = root / "src" / "math.c"
    code = src.read_text() if src.exists() else ""
    notes = root / "notes.md"
    if task == "rename variable":
        if "int count = 3" not in code:
            return (False, "expected `int count = 3` in src/math.c "
                    "(renamed from `int x = 3`), but it is absent")
        if "int x = 3" in code:
            return (False, "`int x = 3` is still in src/math.c; rename "
                    "it to `int count = 3`")
        return (True, "")
    if task == "add function":
        if "double avg(double a, double b)" in code:
            return (True, "")
        return (False, "expected `double avg(double a, double b)` in "
                "src/math.c, but it is absent")
    if task == "fix lint error":
        if "unused_helper" not in code:
            return (True, "")
        return (False, "`unused_helper` is still in src/math.c; remove "
                "the unused function")
    if task == "write note":
        if notes.exists() and "meeting notes" in notes.read_text():
            return (True, "")
        return (False, "expected notes.md containing 'meeting notes'")
    return (True, "")


def _replay_feedback(state, results_path: Path, backend: str,
                    limit: int = 200) -> int:
    """Cross-run learning (spec 9.3): seed the fresh kernel registry
    with semantic feedback from previous suite runs of the SAME backend.
    The router then starts with proven STRAT: rates instead of a cold
    start. Scoped by backend: mixing planner success profiles (local vs
    cloud) would bias the router. Old records without a plan_sig are
    skipped (they predate the feedback loop)."""
    if not results_path.exists():
        return 0
    # replay the MOST RECENT records: the registry must learn the
    # CURRENT system (the blind-write guard changed the capability
    # profile; pre-guard samples would poison the router).
    from collections import deque
    lines = deque(maxlen=limit)
    with results_path.open() as f:
        for line in f:
            lines.append(line)
    n = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (rec.get("backend") == backend and rec.get("plan_sig")
                and rec.get("strategy")
                and ("ok" in rec or "ok_semantic" in rec)):
            ok_flag = (rec["ok_semantic"] if "ok_semantic" in rec
                       else rec["ok"])
            sig = rec["plan_sig"]
            strat = rec["strategy"]
            state.feedback(f"STRAT:{sig}:{strat}", bool(ok_flag))
            # model-scoped keys so the router learns per-model rates
            # (MODEL:<sig>:<model> + model-tagged STRAT keys)
            model = rec.get("model")
            if model:
                state.feedback(
                    f"STRAT:{sig}:{strat}:{model}", bool(ok_flag))
                state.feedback(f"MODEL:{sig}:{model}", bool(ok_flag))
                # query-keyed rate (usable at routing time)
                qsig = rec.get("q_sig")
                if qsig:
                    state.feedback(f"MODEL:{qsig}:{model}",
                                   bool(ok_flag))
            n += 1
    return n


def build_repo(root: Path, scale_kb: int = 0):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "src").mkdir(exist_ok=True)
    for path, content in corpus.FIXTURE.items():
        p = root / path
        p.parent.mkdir(exist_ok=True)
        p.write_text(content)
    if scale_kb > 0:
        # filler modules to approximate a real repo (naive full-context
        # prompts grow linearly with repo size; contained summaries don't)
        line = "int filler_%d(int a) { return a + %d; } // module filler\n"
        wrote = 0
        i = 0
        target = scale_kb * 1024
        while wrote < target:
            with (root / "src" / f"mod{i % 10}.c").open("a") as f:
                for _ in range(150):
                    f.write(line % (i, i))
                    wrote += len(line % (i, i))
            i += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock",
                    choices=["mock", "deepseek", "normal", "ollama",
                             *OPENAI_BACKEND_NAMES],
                    help="mock|deepseek|normal|ollama|openai-compat presets "
                         "(freetoken, llamacpp, vllm, sglang, lmstudio, "
                         "koboldcpp, tabbyapi, openai, openrouter, …)")
    ap.add_argument("--tasks", default=None,
                    help="comma-separated: task index, family name, or 'all'")
    ap.add_argument("--no-escalate", action="store_true",
                    help="disable local->cloud escalation (honest local-only "
                         "success rate; the same-backend re-plan rung still runs)")
    ap.add_argument("--model", default=None,
                    help="local model id (ollama). Default qwen2.5-coder:7b; "
                         "qwen2.5-coder:1.5b fits a 2 GB GPU (full offload)")
    ap.add_argument("--models", default=None,
                    help="comma-separated ORDERED model list for per-task "
                         "routing (harness/router.py): queries get the model "
                         "whose strengths match, and a schema-rejected plan "
                         "retries once with the next model. e.g. "
                         "qwen2.5-coder:1.5b,qwen2.5-coder:7b")
    ap.add_argument("--scale-kb", type=int, default=0,
                    help="grow the scratch repo to ~N KB with filler modules "
                         "(demonstrates naive-prompt scaling vs containment)")
    ap.add_argument("--results", default=None,
                    help="JSONL results log (default: "
                         "~/.local/state/swarmstate/task_suite.jsonl)")
    args = ap.parse_args()
    results_path = Path(args.results or
                        Path.home() / ".local/state/swarmstate" / "task_suite.jsonl")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[task_suite] kernel: {KERNEL_VERSION} (backend={args.backend}, "
          f"escalate={not args.no_escalate})", flush=True)

    tasks = TASKS
    if args.tasks:
        sel = []
        for tok in args.tasks.split(","):
            tok = tok.strip()
            if tok.isdigit():
                sel.append(TASKS[int(tok)])
            elif tok in corpus.FAMILIES:
                sel.extend(corpus.FAMILIES[tok])
            elif tok in ("all", "corpus"):
                sel.extend(corpus.ALL_TASKS)
            else:  # exact task name (canonical or corpus family task)
                hit = next((t for t in list(TASKS) + list(corpus.ALL_TASKS)
                            if t[0] == tok), None)
                if hit:
                    sel.append(hit)
                else:
                    print(f"unknown task/family: {tok}", file=sys.stderr)
        tasks = sel

    with tempfile.TemporaryDirectory(prefix="swarmstate-suite-") as tmp:
        root = Path(tmp)
        build_repo(root, scale_kb=args.scale_kb)
        print(f"repo: {root}  backend: {args.backend}  scale_kb={args.scale_kb}\n")
        print(f"{'task':<22} {'strategy':<10} {'ops':<4} {'bytes':>7} {'ms':>7}  ok")
        print("-" * 68)
        env = ({"model": args.model,
                "gpu": gpu.gpu_label(),
                "processor": gpu.ollama_processor(),
                "ngl": int(os.environ.get("SS_OLLAMA_NGL", "0") or 0)}
               if args.backend in ("ollama", "normal", "deepseek") else {})
        with Orchestrator(str(root), backend=args.backend,
                          model=args.model,
                          models=args.models.split(",") if args.models
                          else None) as orch:
            replayed = _replay_feedback(orch.state, results_path, args.backend)
            if replayed:
                print(f"[task_suite] seeded {replayed} prior feedback "
                      f"samples into the strategy registry (spec 9.3)\n")
            for name, query in tasks:
                run_one(orch, root, results_path, name, query,
                        backend=args.backend, scale_kb=args.scale_kb,
                        escalate=not args.no_escalate, env=env)
        print(f"results -> {results_path}")
    return 0


def run_one(orch, root: Path, results_path: Path, name: str, query: str,
            backend: str, scale_kb: int = 0, escalate: bool = True,
            env: dict | None = None) -> dict:
    """Run one task through the orchestrator: execute, parse the turn,
    apply the semantic oracle, feed the verdict back into the registry,
    and append the record. Returns the record. Shared by the canonical
    suite and scripts/corpus_builder.py. `env` (e.g. model + GPU info)
    is merged into the record for later correlation."""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    t0 = time.monotonic()
    res_before = orch.state.sysinfo() if hasattr(orch, "state") else {}
    out = ""
    try:
        out = orch.ask(query, escalate=escalate)
        strat, ops, failed, fail_reason = _parse_turn(out)
        ctx_bytes = sum(len(l) + 1 for l in out.splitlines())
        ok = not failed
        # Gate the semantic oracle on MECHANICAL success. A schema-
        # rejected plan never executed, so the repo state trivially
        # "satisfies" no-op oracles (list/explain tasks) — recording
        # that as semantic-OK would poison the registry ("rejected
        # plan for list fns -> semantic yes"). Nothing ran -> no verdict.
        ok_semantic = None
        oracle_retried = 0
        reason = ""
        if ok:
            ok_semantic, reason = _semantic_ok(name, root, out)
            if not ok_semantic:
                # oracle-in-the-loop retry (bounded, once): the plan
                # EXECUTED but did not achieve the task (the dominant
                # WEAK mode: writes the file without the edit). Feed
                # the oracle's reason back and re-plan — preferring the
                # OTHER model when the router configured several, since
                # the two local models fail differently (7B echoes,
                # 1.5B truncates).
                alt = (orch.other_model()
                       if hasattr(orch, "other_model") else None)
                out2 = orch.ask(query, escalate=escalate,
                                feedback=reason, force_model=alt)
                oracle_retried = 1
                strat2, ops2, failed2, reason2 = _parse_turn(out2)
                if not failed2:
                    ok_sem2, reason3 = _semantic_ok(name, root, out2)
                    if ok_sem2:
                        out, strat, ops, fail_reason = (
                            out2, strat2, ops2, reason2)
                        ok_semantic = True
                        reason = ""
        verdict = "ok" if (ok and ok_semantic) else (
            "WEAK" if ok else "FAIL")
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        res_after = orch.state.sysinfo()
        print(f"{name:<30} {strat:<10} {ops:<4} {ctx_bytes:>7} "
              f"{duration_ms:>7.0f}  {verdict}", flush=True)
        # close the learning loop: record SEMANTIC y/n against
        # STRAT:<plan-sig>:<strategy> so the registry router (spec 9.3)
        # learns what actually works, not just what avoided a kernel error.
        orch.feedback(ok and ok_semantic)
        usage = getattr(orch, "last_usage", {}) or {}
        rec = {
            "ts": started_at, "backend": backend,
            "scale_kb": scale_kb,
            "task": name, "strategy": strat, "ops": ops,
            **({} if not env else env),
            "model": getattr(orch, "last_model", None) or (env or {}).get("model"),
            "plan_sig": getattr(orch, "last_plan_sig", None),
            "ctx_bytes": ctx_bytes, "ok": ok,
            "ok_semantic": ok_semantic,
            "retried": getattr(orch, "last_retries", 0),
            "oracle_retried": oracle_retried,
            "q_sig": getattr(orch, "last_query_sig", None),
            "write_ctx": getattr(orch, "last_write_ctx", 0),
            "duration_ms": duration_ms,
            "prompt_bytes": getattr(orch, "last_prompt_bytes", 0),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "mem_avail_kb_start": res_before.get("mem_avail_kb"),
            "mem_avail_kb_end": res_after.get("mem_avail_kb"),
            "load1_start": res_before.get("load1"),
            "load1_end": res_after.get("load1"),
            "res_tag": orch.state.resource_tag(),
            "error": fail_reason,
            # raw orchestrator turn for diagnosing WEAK/FAIL: what the
            # model actually planned and what the kernel said
            "turn": out[:4000],
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        diag = f"{exc} | out={out[:200]!r}" if out else str(exc)
        print(f"{name:<30} {'-':<10} {'-':<4} {'-':>7} "
              f"{duration_ms:>7.0f}  ERROR {diag}", flush=True)
        rec = {
            "ts": started_at, "backend": backend,
            "scale_kb": scale_kb,
            "task": name, "ok": False, "duration_ms": duration_ms,
            "error": str(exc),
            **({} if not env else env),
        }
    _log_result(results_path, rec)
    return rec


def _parse_turn(out: str) -> tuple[str, str, bool, str]:
    """Extract strategy/ops from the orchestrator's [turn ...] line and
    decide the mechanical verdict. NEVER raises on unusual output: a
    rejected plan has no ops=/strategy= section, so parsing used to die
    with a raw IndexError ('list index out of range') that hid the real
    failure. Now the raw [turn line becomes the failure reason instead.
    Returns (strat, ops, failed, reason)."""
    turn_line = next((l for l in out.splitlines()
                      if l.startswith("[turn")), "")
    strat = (turn_line.split("strategy=")[1].split(" ")[0]
             if "strategy=" in turn_line else "")
    ops = (turn_line.split("ops=")[1].split("]")[0] + "]"
           if "ops=" in turn_line else "")
    failed = ("plan rejected" in out or "planner failed" in out
              or "governance:" in out or "!!" in out)
    reason = ""
    if failed:
        # most informative line first: details (schema errors / op error)
        # beat the generic [turn ...] header
        for k in ("schema errors", "governance:", "!!",
                  "planner failed", "plan rejected"):
            line = next((l.strip() for l in out.splitlines() if k in l), "")
            if line:
                reason = line
                break
    return strat, ops, failed, reason


def _log_result(path: Path, rec: dict) -> None:
    """Append one JSONL line per task run (temporal record for later
    mock-vs-cloud analysis; also captures resource conditions)."""
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    sys.exit(main())
