#!/usr/bin/env python3
"""StatePod orchestrator CLI.

Interactive:
    python3 harness/main.py /path/to/repo [--backend mock|ollama|freetoken|llamacpp] [--model …]

One-shot:
    python3 harness/main.py /path/to/repo --query "grep 'TODO'"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

from orchestrator import Orchestrator  # noqa: E402
from planner import OPENAI_BACKEND_NAMES  # noqa: E402

_BACKEND_CHOICES = ["mock", "ollama", "deepseek", *OPENAI_BACKEND_NAMES]


def main() -> int:
    ap = argparse.ArgumentParser(description="StatePod orchestrator")
    ap.add_argument("root", help="repo directory the kernel operates on")
    ap.add_argument("--backend", default="mock",
                    choices=_BACKEND_CHOICES)
    ap.add_argument("--model", default=None,
                    help="model id (ollama / OpenAI-compat backends / deepseek)")
    ap.add_argument("--query", help="one-shot query (default: interactive REPL)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--plan-grammar", default="lenient",
                    choices=["lenient", "strict"],
                    help="plan grammar gate (strict rejects the WHOLE plan "
                         "with actionable errors)")
    args = ap.parse_args()

    with Orchestrator(args.root, backend=args.backend, model=args.model,
                      verbose=args.verbose,
                      plan_grammar=args.plan_grammar) as orch:
        if args.query:
            print(orch.ask(args.query))
            return 0
        print(f"StatePod orchestrator on {args.root} "
              f"(backend={args.backend}). Type a query, or 'exit'.")
        while True:
            try:
                q = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not q:
                continue
            if q.lower() in ("exit", "quit"):
                break
            print(orch.ask(q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
