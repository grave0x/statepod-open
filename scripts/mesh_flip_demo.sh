#!/usr/bin/env bash
# mesh_flip_demo.sh -- contradictory-evidence experiment (GAPS #9 demo).
#
# Story: Node A learned (from its own painful history) that strategy DELTA
# fails on WRITE tasks (minimal context -> blind writes) and that FULL
# succeeds (full context).  Node B, having joined the mesh and absorbed A's
# evidence through the registry bridge, REFUSES DELTA for WRITE and picks
# FULL -- while a control node with no mesh sticks with the DELTA heuristic
# default.  (TARGETED is not used: for pure-WRITE plans it degrades to
# DELTA by design -- no readable paths to target.)
#
#   "Node A learned something harmful; Node B refused to repeat it."
#
# All mock backend (deterministic, cheap).  The strategy choice is the
# observable, not the verdict.
set -u
cd "$(dirname "$0")/.."

R_MESH=/tmp/ss_flip_mesh.jsonl
R_CTRL=/tmp/ss_flip_ctrl.jsonl
rm -f "$R_MESH" "$R_CTRL" /tmp/ss_flip_hub.log
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

# Node A: legacy node sharing its hard-won (negative) learning about WRITE
python3 harness/meshd.py --name node-a --port 5501 < /dev/null \
  --publish $'reg/STRAT/WRITE/DELTA\t0' \
  --publish $'reg/STRAT/WRITE/DELTA\t0' \
  --publish $'reg/STRAT/WRITE/DELTA\t0' \
  --publish $'reg/STRAT/WRITE/DELTA\t0' \
  --publish $'reg/STRAT/WRITE/FULL\t1' \
  --publish $'reg/STRAT/WRITE/FULL\t1' \
  --hash-interval 2 > /tmp/ss_flip_hub.log 2>&1 &
APID=$!
sleep 1

echo "=== Node B WITH mesh (absorbs A's evidence via hub/bridge) ==="
python3 scripts/run_named.py --backend mock \
  --names "rename tmp->buffer@src/util.c,add fn int max@src/util.c,lint remove ptr@src/util.c,add fn int clamp@src/util.c" \
  --results "$R_MESH" \
  --mesh-name field-2 --mesh-port 5502 --mesh-peer 127.0.0.1:5501 2>&1 | grep -E "ok$|WEAK|FAIL"

echo ""
echo "=== CONTROL (no mesh, cold heuristic) ==="
python3 scripts/run_named.py --backend mock \
  --names "rename tmp->buffer@src/util.c,add fn int max@src/util.c,lint remove ptr@src/util.c,add fn int clamp@src/util.c" \
  --results "$R_CTRL" 2>&1 | grep -E "ok$|WEAK|FAIL"

kill -INT $APID 2>/dev/null
sleep 1

echo ""
echo "=== STRATEGY COMPARISON ==="
python3 - <<'EOF'
import json
def strat(p):
    out = []
    for l in open(p):
        r = json.loads(l)
        out.append((r.get("task", "?")[:24], r.get("plan_sig"), r.get("strategy")))
    return out
m = strat("/tmp/ss_flip_mesh.jsonl")
c = strat("/tmp/ss_flip_ctrl.jsonl")
print(f"  {'task':24} {'sig':8} {'WITH-mesh':10} {'control':10}")
for (t1, s1, sm), (t2, s2, sc) in zip(m, c):
    flip = "  <-- FLIP" if sm != sc else ""
    print(f"  {t1:24} {s1:8} {sm:10} {sc:10}{flip}")
EOF
