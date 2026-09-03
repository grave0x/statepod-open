#!/usr/bin/env bash
# plan_grammar_demo.sh -- the valid-plan guarantee (spec v1).
#
# Two layers keep malformed plans from reaching the kernel:
#   1. PLAN_GBNF (harness/plan_grammar.py): a llama.cpp grammar that
#      CONSTRAINS generation -- a grammar-capable backend cannot emit
#      an unknown op type, bad strategy, or wrong shape at all.
#   2. strict gate: make_plan(grammar="strict") validates every
#      backend's output (mock/deepseek/ollama/mesh) and REJECTS the
#      whole plan with actionable errors instead of silently dropping
#      malformed ops (the old lenient behavior).
set -u
cd "$(dirname "$0")/.."
export SWARMSTATE_LIB=$PWD/libswarmstate.so

echo "=== plan grammar: generation constraint + strict gate ==="
python3 - <<'EOF'
import sys; sys.path.insert(0, "harness"); sys.path.insert(0, "py")
import planner
from plan_grammar import PLAN_GBNF, validate

print("  artifact: PLAN_GBNF (%d lines, llama.cpp format)" % len(PLAN_GBNF.splitlines()))
print("    " + PLAN_GBNF.strip().splitlines()[1].strip())

# 1) a malformed plan the OLD path would silently mangle
bad = {"ops": [{"type": "BOGUS", "path": 7}, {"type": "READ"}],
       "strategy": "MOO"}
ok, errs = validate(bad)
print("\n  strict gate on malformed plan: rejected with")
for e in errs:
    print("    -", e)

# 2) end-to-end: make_plan(grammar="strict") refuses it
orig = planner.plan_mock
planner.plan_mock = lambda q, s: bad
try:
    planner.make_plan("x", "y", backend="mock", grammar="strict")
    print("\n  FAIL: strict accepted a malformed plan")
except ValueError as exc:
    print("\n  make_plan(grammar=strict) -> ValueError:")
    print("    ", exc)
finally:
    planner.plan_mock = orig

# 3) valid plans still flow (regression)
p = planner.make_plan("list the TODO markers in a.c", "summary",
                      backend="mock", grammar="strict")
print("\n  strict accepts valid plan:", p["ops"], p["strategy"])
print("\n  plan grammar demo PASS")
EOF
