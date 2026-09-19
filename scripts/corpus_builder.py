#!/usr/bin/env python3
"""Corpus builder: sample parameterized task families and run them through
the orchestrator loop, feeding semantic verdicts back into the §9.3
registry so STRAT: samples accumulate across families/signatures.

Usage:
  python3 scripts/corpus_builder.py --backend mock            # fast (~50/s)
  python3 scripts/corpus_builder.py --backend ollama --n 20 --budget-min 20
  python3 scripts/corpus_builder.py --families rename,explain --n 10

Every task gets a FRESH scratch repo (edit tasks mutate files, so the
corpus cannot share one repo). Results append to the SAME jsonl the
canonical suite uses, so registry replay + compare_suites see the whole
history. A time budget makes long runs safe to background overnight.

End-of-run summary: per-family ok/sem/weak rates and, per plan signature,
which strategy actually wins (the data that steers the router).
"""
import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import corpus
import gpu
from orchestrator import Orchestrator
from planner import OPENAI_BACKEND_NAMES
from task_suite import build_repo, run_one, _replay_feedback
try:
    from meshd import MeshDaemon, registry_bridge
except OSError:          # libmesh.so missing: mesh is optional
    MeshDaemon = registry_bridge = None
from statepod import KERNEL_VERSION

DEFAULT_RESULTS = Path.home() / ".local/state/statepod" / "task_suite.jsonl"


def summarize(recs, results_path, backend):
    """Per-family + per-signature strategy summary from the records."""
    fam = defaultdict(list)
    for r in recs:
        fam[r["task"].split()[0]].append(r)
    print(f"\n=== corpus summary ({backend}) — {len(recs)} tasks ===")
    print(f"{'family':<14} {'n':>4} {'ok':>4} {'sem':>4} {'weak':>4} {'avg_ms':>8}")
    for name in sorted(fam):
        rs = fam[name]
        n = len(rs)
        ok = sum(1 for r in rs if r.get("ok"))
        sem = sum(1 for r in rs if r.get("ok_semantic") is True)
        weak = sum(1 for r in rs if r.get("ok") and r.get("ok_semantic") is False)
        avg = sum(r.get("duration_ms", 0) for r in rs) / max(n, 1)
        print(f"{name:<14} {n:>4} {ok:>4} {sem:>4} {weak:>4} {avg:>8.0f}")
    # per-signature strategy winners (the router's view)
    sig = defaultdict(lambda: defaultdict(list))
    for r in recs:
        if r.get("plan_sig") and r.get("strategy"):
            sig[r["plan_sig"]][r["strategy"]].append(
                r.get("ok_semantic", r.get("ok")))
    print("\n=== per-signature strategy rates (semantic) ===")
    for s in sorted(sig):
        parts = []
        for strat, oks in sorted(sig[s].items(),
                                 key=lambda kv: -sum(1 for o in kv[1] if o)):
            n = len(oks)
            rate = sum(1 for o in oks if o) / n
            parts.append(f"{strat} {rate:.0%} ({n})")
        print(f"  {s:<34} " + "  ".join(parts))
    # resource picture
    loads = [r.get("load1_start") for r in recs if r.get("load1_start")]
    if loads:
        print(f"\nload1 start range: {min(loads):.1f}–{max(loads):.1f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock",
                    choices=["mock", "deepseek", "normal", "ollama", "mesh",
                             *OPENAI_BACKEND_NAMES])
    ap.add_argument("--families", default="all",
                    help="comma-separated family names (see corpus.FAMILIES) "
                         "or 'all'")
    ap.add_argument("--n", type=int, default=0,
                    help="max tasks to run (0 = whole pool, then repeat to "
                         "fill the budget)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--budget-min", type=float, default=0,
                    help="stop after this many minutes (0 = unlimited)")
    ap.add_argument("--no-escalate", action="store_true")
    ap.add_argument("--model", default=None,
                    help="local model id (ollama). Default qwen2.5-coder:7b; "
                         "qwen2.5-coder:1.5b fits a 2 GB GPU (full offload)")
    ap.add_argument("--models", default=None,
                    help="comma-separated ORDERED model list for per-task "
                         "routing (harness/router.py); schema-rejected plans "
                         "retry with the next model")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--scale-kb", type=int, default=0)
    ap.add_argument("--mesh-name", default=None,
                    help="start an in-process mesh peer (peer id)")
    ap.add_argument("--mesh-port", type=int, default=0,
                    help="mesh listen port (requires --mesh-name)")
    ap.add_argument("--mesh-peer", action="append", default=[],
                    metavar="HOST:PORT", help="mesh outbound peer (repeatable)")
    ap.add_argument("--mesh-allow", default="",
                    help="comma-separated peer ids allowed to inject ops")
    args = ap.parse_args()

    results_path = Path(args.results)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    pool = corpus.ALL_TASKS
    if args.families != "all":
        pool = []
        for f in args.families.split(","):
            pool.extend(corpus.FAMILIES.get(f.strip(), []))
    if not pool:
        print("empty task pool", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    order = list(pool)
    rng.shuffle(order)
    if args.n:
        order = (order * (args.n // len(order) + 1))[: args.n]

    # optional in-process mesh: live registry learning shared with peers.
    # The on_op bridge feeds inbound mesh ops into whichever kernel
    # registry is current (per-task Orchestrator).
    mesh = None
    current_state = {"s": None}
    pending_ops = []
    if args.mesh_name:
        if MeshDaemon is None:
            print("--mesh-name requires libmesh.so (see meshbridge.py)",
                  file=sys.stderr)
            return 1
        if not args.mesh_port:
            print("--mesh-port required with --mesh-name", file=sys.stderr)
            return 1
        mesh_peers = []
        for spec in args.mesh_peer:
            host, _, port = spec.rpartition(":")
            mesh_peers.append((host or "127.0.0.1", int(port)))
        mesh_allow = [x for x in args.mesh_allow.split(",") if x] or None

        def _on_op(t, v, holder=current_state, pending=pending_ops):
            # ALWAYS accumulate (per-task registries are ephemeral; each
            # new task re-flushes the full mesh learning).  Also record
            # live into the current registry when one is open.
            pending.append((t, v))
            st = holder["s"]
            if st is not None:
                try:
                    registry_bridge(st)(t, v)
                except Exception:
                    pass

        mesh = MeshDaemon(args.mesh_name, args.mesh_port, mesh_peers,
                          mesh_allow, on_op=_on_op)
        mesh.start()
        if args.mesh_peer:
            # let the tunnel come up and catch-up flow BEFORE the first
            # registry-gated decision (otherwise the first task sees a
            # half-empty registry and the least-bad fallback fires)
            mesh.ready.wait(3)
            time.sleep(0.2)

    print(f"[corpus_builder] kernel: {KERNEL_VERSION} backend={args.backend} "
          f"pool={len(pool)} tasks={len(order)} seed={args.seed} "
          f"budget={args.budget_min}min", flush=True)

    recs = []
    t_start = time.monotonic()
    for i, (name, query) in enumerate(order, 1):
        if args.budget_min and (time.monotonic() - t_start) / 60 >= args.budget_min:
            print(f"[corpus_builder] budget reached at task {i-1}", flush=True)
            break
        with tempfile.TemporaryDirectory(prefix="statepod-corpus-") as tmp:
            root = Path(tmp)
            build_repo(root, scale_kb=args.scale_kb)
            with Orchestrator(str(root), backend=args.backend,
                              model=args.model,
                              models=args.models.split(",") if args.models
                              else None, mesh=mesh) as orch:
                current_state["s"] = orch.state
                if mesh is not None and pending_ops:
                    # re-seed this task's fresh registry with the full
                    # mesh learning (each registry is per-task ephemeral)
                    for _t, _v in pending_ops:
                        registry_bridge(orch.state)(_t, _v)
                _replay_feedback(orch.state, results_path, args.backend)
                env = ({"model": args.model,
                        "gpu": gpu.gpu_label(),
                        "processor": gpu.ollama_processor(),
                        "ngl": int(os.environ.get("SP_OLLAMA_NGL", "0") or 0)}
                       if args.backend in ("ollama", "normal", "deepseek")
                       else {})
                recs.append(run_one(orch, root, results_path, name, query,
                                    backend=args.backend,
                                    scale_kb=args.scale_kb,
                                    escalate=not args.no_escalate, env=env))
        if i % 10 == 0:
            print(f"[corpus_builder] {i}/{len(order)} done "
                  f"({(time.monotonic()-t_start)/60:.1f} min)", flush=True)

    summarize(recs, results_path, args.backend)
    if mesh is not None:
        # final convergence printout: our folded mesh state hash
        st = mesh.peer.state_json()
        import hashlib
        print(f"[corpus_builder] mesh state hash "
              f"{hashlib.sha256(st.encode()).hexdigest()[:16]} "
              f"tail={mesh.peer.stats()['tail_ops']}", flush=True)
        mesh.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
