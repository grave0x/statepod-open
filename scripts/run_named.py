#!/usr/bin/env python3
"""Run a specific list of corpus tasks by name (targeted re-runs).

Useful after a budget-capped corpus run to finish the remaining tasks,
and for WEAK/FAIL forensics: pass --show-turns to print the raw
orchestrator output each task produced (what the model planned, what
the kernel rejected).

Usage:
  python3 scripts/run_named.py --names "rename tmp->buffer@src/util.c,grep HACK" \
      --backend ollama --no-escalate [--model qwen2.5-coder:1.5b] [--show-turns]
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus
import gpu
from orchestrator import Orchestrator
from planner import OPENAI_BACKEND_NAMES
from task_suite import build_repo, run_one, _replay_feedback
try:
    from meshd import MeshDaemon, registry_bridge
except OSError:
    MeshDaemon = registry_bridge = None

DEFAULT_RESULTS = Path.home() / ".local/state/statepod/task_suite.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True, help="comma-separated task names")
    ap.add_argument("--backend", default="ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--models", default=None,
                    help="comma-separated ORDERED model list for routing")
    ap.add_argument("--no-escalate", action="store_true")
    ap.add_argument("--show-turns", action="store_true")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--mesh-name", default=None,
                    help="start an in-process mesh peer (peer id)")
    ap.add_argument("--mesh-port", type=int, default=None,
                    help="mesh listen port (requires --mesh-name)")
    ap.add_argument("--mesh-peer", action="append", default=[],
                    metavar="HOST:PORT", help="mesh outbound peer (repeatable)")
    ap.add_argument("--mesh-allow", default="",
                    help="comma-separated peer ids allowed to inject ops")
    ap.add_argument("--governance", default="off",
                    choices=["off", "enforce", "interactive"],
                    help="authorization policy for governed ops (spec v1 "
                         "section 6); interactive prompts the human")
    ap.add_argument("--governed-op", action="append", default=[],
                    metavar="OP", help="op type requiring auth+reason "
                                       "(repeatable, e.g. --governed-op EXECUTE)")
    ap.add_argument("--supervisor-secret", default=None,
                    help="HMAC secret for supervisor tokens (env: "
                         "STATEPOD_SUPERVISOR_SECRET)")
    ap.add_argument("--mock-auth-token", default=None,
                    help=("stamp mock plans' governed ops with this "
                          "supervisor token (deterministic demo path)"))
    ap.add_argument("--governance-log", default=None,
                    help="audit log path (default: <results>.governance.log)")
    ap.add_argument("--pool-invite", default=None,
                   help="signed pool invite to present to the hub on join")
    ap.add_argument("--plan-grammar", default="lenient",
                    choices=["lenient", "strict"],
                    help="plan grammar gate (strict rejects the"
                         " WHOLE plan with actionable errors)")
    args = ap.parse_args()

    by_name = {n: q for n, q in corpus.ALL_TASKS}
    want = [n.strip() for n in args.names.split(",") if n.strip()]
    missing = [n for n in want if n not in by_name]
    if missing:
        sys.exit(f"unknown tasks: {missing}")

    env = ({"model": args.model, "gpu": gpu.gpu_label(),
            "processor": gpu.ollama_processor(),
            "ngl": int(os.environ.get("SP_OLLAMA_NGL", "0") or 0)}
           if args.backend in ("ollama", "normal", "deepseek",
                               *OPENAI_BACKEND_NAMES) else {})

    # optional in-process mesh: live registry learning shared with peers.
    # The on_op bridge feeds inbound mesh ops into whichever kernel
    # registry is current (per-task Orchestrator) -- so peer learning
    # actually steers the local router/strategy gate.
    mesh = None
    current_state = {"s": None}
    pending_ops = []
    if args.mesh_name and MeshDaemon is None:
        sys.exit("--mesh-name requires libmesh.so (see meshbridge.py)")
    if args.mesh_name:
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
                          mesh_allow, on_op=_on_op,
                          join_invite=args.pool_invite)
        mesh.start()
        if args.mesh_peer:
            # let the tunnel come up and catch-up flow BEFORE the first
            # registry-gated decision (otherwise the first task sees a
            # half-empty registry and the least-bad fallback fires)
            import time as _time
            mesh.ready.wait(3)
            _time.sleep(0.2)

    for name in want:
        with tempfile.TemporaryDirectory(prefix="statepod-named-") as tmp:
            root = Path(tmp)
            build_repo(root)
            from governance import Governance
            gov_log = (Path(args.governance_log) if args.governance_log
                       else Path(args.results).with_suffix(".governance.log"))
            gov = Governance(mode=args.governance,
                             governed_ops=set(args.governed_op),
                             secret=(args.supervisor_secret
                                     or os.environ.get(
                                         "STATEPOD_SUPERVISOR_SECRET")),
                             log_path=gov_log)
            with Orchestrator(str(root), backend=args.backend,
                              model=args.model,
                              models=args.models.split(",") if args.models
                              else None, mesh=mesh,
                              governance=gov,
                              mock_auth_token=args.mock_auth_token,
                              plan_grammar=args.plan_grammar) as orch:
                current_state["s"] = orch.state
                if mesh is not None and pending_ops:
                    # re-seed this task's fresh registry with the full
                    # mesh learning (each registry is per-task ephemeral)
                    for _t, _v in pending_ops:
                        registry_bridge(orch.state)(_t, _v)
                _replay_feedback(orch.state, Path(args.results), args.backend)
                rec = run_one(orch, root, Path(args.results), name,
                              by_name[name], backend=args.backend,
                              escalate=not args.no_escalate, env=env)
        if args.show_turns:
            turn = rec.get("turn") or ""
            print(f"\n--- {name} ---")
            print(turn[:1200] if turn else "(no turn captured)")
            print("--- end ---\n")


if __name__ == "__main__":
    main()
