"""Compare task-suite runs (mock vs. real-LLM) from the temporal JSONL log.

Usage: python3 scripts/compare_suites.py [PATH]
Default PATH: ~/.local/state/swarmstate/task_suite.jsonl

Prints per-backend aggregates (context bytes, duration, strategy mix) and a
per-task side-by-side, so success AND speed can be compared over time.
"""
import collections
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else
            Path.home() / ".local/state/swarmstate" / "task_suite.jsonl")
recs = [json.loads(l) for l in path.open()] if path.exists() else []
if not recs:
    print(f"no records at {path}")
    sys.exit(1)

by_run = collections.defaultdict(list)
for r in recs:
    by_run[(r["backend"], r.get("scale_kb", 0))].append(r)

def _cost(rs, in_per_m=0.27, out_per_m=1.10):
    """Approx API cost from logged tokens. Rates are env-overridable
    (SS_COST_IN_PER_M / SS_COST_OUT_PER_M), defaulting to DeepSeek-like
    values; mark results as approximate when rates are guesses."""
    import os
    in_per_m = float(os.environ.get("SS_COST_IN_PER_M", in_per_m))
    out_per_m = float(os.environ.get("SS_COST_OUT_PER_M", out_per_m))
    pt = sum(r.get("prompt_tokens") or 0 for r in rs)
    ct = sum(r.get("completion_tokens") or 0 for r in rs)
    return pt, ct, (pt / 1e6 * in_per_m + ct / 1e6 * out_per_m)


def _sem(r):
    """Semantic verdict for a record: prefer ok_semantic, fall back to ok."""
    return bool(r.get("ok_semantic", r.get("ok")))


def _verdict(r):
    ok = bool(r.get("ok"))
    sem = _sem(r)
    return "ok" if (ok and sem) else ("WEAK" if ok else "FAIL")


def summarize(name, rs):
    if not rs:
        print(f"{name}: (no runs)"); return
    b = [r["ctx_bytes"] for r in rs if "ctx_bytes" in r]
    pb = [r.get("prompt_bytes") for r in rs if r.get("prompt_bytes") is not None]
    ms = [r["duration_ms"] for r in rs]
    strat = collections.Counter(r.get("strategy") for r in rs)
    ok = sum(1 for r in rs if r["ok"])
    sem = sum(1 for r in rs if _sem(r))
    weak = sum(1 for r in rs if bool(r["ok"]) and not _sem(r))
    retried = sum(r.get("retried", 0) for r in rs)
    retried_s = (f" retried={retried}" if retried else "")
    bavg = f"{sum(b)/len(b):.0f}B ({min(b)}-{max(b)})" if b else "-"
    pt, ct, cost = _cost(rs)
    cost_s = f"${cost:.4f}" if (pt + ct) else "-"
    prompt_s = (f"{sum(pb)/len(pb):.0f}B" if pb else "-")
    print(f"{name:<10} n={len(rs):<3} ok={ok}/{len(rs)} sem={sem}/{len(rs)}"
          f"{(' weak=' + str(weak)) if weak else '':<8} "
          f"reply={bavg:<14} prompt={prompt_s:<8} "
          f"tok={pt + ct:<6} cost~{cost_s:<9} "
          f"dur={sum(ms)/len(ms):.0f}ms ({min(ms):.0f}-{max(ms):.0f})  "
          f"strategies={dict(strat)}{retried_s}")

print(f"=== task-suite runs in {path} ===")
for (backend, scale), rs in sorted(by_run.items()):
    summarize(f"{backend}@{scale}KB" if scale else backend, rs)

print("\n=== per-task side-by-side (last run per backend) ===")
last = {}
for r in recs:
    last[r["backend"] + "/" + r["task"]] = r
by_task = collections.defaultdict(dict)
for k, r in last.items():
    by_task[r["task"]][r["backend"]] = r
backends = sorted({r["backend"] for r in recs})
hdr = f"{'task':<22}" + "".join(f"{b:>20}" for b in backends)
print(hdr)
for task in sorted(by_task):
    row = f"{task:<22}"
    for b in backends:
        r = by_task[task].get(b)
        if r is None:
            cell = "-"
        else:
            cell = f"{r.get('strategy','-')} {int(r.get('duration_ms',0))}ms"
            v = _verdict(r)
            if v != "ok":
                cell += f" [{v}]"
        row += f"{cell:>20}"
    print(row)
