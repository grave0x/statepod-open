#!/usr/bin/env python3
"""Maturity gate (build-plan-v1 phase 0): rate-based vs heuristic strategy
picks over the 42-task corpus. Mock backend; writes a JSON report.

Pass: rate semantic_rate >= heuristic, and injected STRAT overrides honor.
# ponytail: mock can't show live semantic win; ollama --backend later.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import corpus
from orchestrator import Orchestrator
from planner import STRAT_MIN_SAMPLES, decide_strategy, plan_signature
from task_suite import build_repo, run_one

REPORT = Path.home() / ".local/state/statepod" / "maturity-bench-latest.json"
OVERRIDE_SIG = "READ"  # plan_signature([{type:READ,path:...}])


def _run(mode: str, results: Path, seed_override: bool) -> list[dict]:
    recs = []
    for name, query in corpus.ALL_TASKS:
        with tempfile.TemporaryDirectory(prefix="ss-mat-") as tmp:
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(
                str(root), backend="mock",
                use_registry_strategy=(mode == "rates"),
            ) as orch:
                if mode == "rates":
                    # replay prior heuristic run + synthetic override
                    from task_suite import _replay_feedback
                    _replay_feedback(orch.state, results, "mock")
                    if seed_override:
                        for _ in range(STRAT_MIN_SAMPLES):
                            orch.state.feedback(f"STRAT:{OVERRIDE_SIG}:DELTA", True)
                            orch.state.feedback(
                                f"STRAT:{OVERRIDE_SIG}:TARGETED", False)
                recs.append(run_one(
                    orch, root, results, name, query, backend="mock"))
    return recs


def _sem_rate(recs: list[dict]) -> float:
    oks = [r for r in recs if r.get("ok_semantic") is not None]
    if not oks:
        return 0.0
    return sum(1 for r in oks if r["ok_semantic"]) / len(oks)


def _by_sig(recs: list[dict]) -> dict:
    out = defaultdict(lambda: defaultdict(list))
    for r in recs:
        sig = r.get("plan_sig") or "?"
        out[sig][r.get("strategy") or "?"].append(r.get("ok_semantic"))
    return {
        s: {st: {"n": len(v), "sem": sum(1 for x in v if x) / len(v)}
            for st, v in strats.items()}
        for s, strats in out.items()
    }


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ss-mat-res-") as td:
        heur_path = Path(td) / "heur.jsonl"
        rate_path = Path(td) / "rate.jsonl"
        print(f"[maturity] heuristic pass ({len(corpus.ALL_TASKS)} tasks)",
              flush=True)
        heur = _run("heuristic", heur_path, seed_override=False)
        print(f"[maturity] rates pass (override STRAT:{OVERRIDE_SIG})",
              flush=True)
        # rates pass reads heuristic jsonl via replay from a shared path
        shared = Path(td) / "shared.jsonl"
        shared.write_text(heur_path.read_text())
        rates = _run("rates", shared, seed_override=True)

    # unit check: override honored on a bare READ plan
    with tempfile.TemporaryDirectory() as td2:
        with Orchestrator(td2, backend="mock") as orch:
            for _ in range(STRAT_MIN_SAMPLES):
                orch.state.feedback(f"STRAT:{OVERRIDE_SIG}:DELTA", True)
                orch.state.feedback(f"STRAT:{OVERRIDE_SIG}:TARGETED", False)
            picked, _ = decide_strategy(
                [{"type": "READ", "path": "x.c"}], registry=orch.state)
    override_ok = picked == "DELTA"

    hs, rs = _sem_rate(heur), _sem_rate(rates)
    report = {
        "tasks": len(corpus.ALL_TASKS),
        "strat_min_samples": STRAT_MIN_SAMPLES,
        "heuristic_semantic_rate": hs,
        "rates_semantic_rate": rs,
        "override_honored": override_ok,
        "per_sig_heuristic": _by_sig(heur),
        "per_sig_rates": _by_sig(rates),
        "pass": override_ok and rs >= hs,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "tasks", "heuristic_semantic_rate", "rates_semantic_rate",
        "override_honored", "pass")}, indent=2))
    print(f"[maturity] wrote {REPORT}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    # self-check: STRAT_MIN_SAMPLES gate
    assert STRAT_MIN_SAMPLES >= 3
    raise SystemExit(main())
