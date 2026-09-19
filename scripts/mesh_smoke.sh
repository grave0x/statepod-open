#!/usr/bin/env bash
# mesh_smoke.sh -- StatePod mesh Phase 1 convergence test (GAPS #9).
#
# Two nodes exchange >=2 registry STRAT samples each way over plaintext-LAN
# TCP and must converge on ONE shared state hash:
#   sha256(mesh_peer_state_json()) identical on both sides.
#
# Requires libmesh.so (see harness/meshbridge.py build note).
set -u
cd "$(dirname "$0")/.."

A_LOG=/tmp/sp_meshA.log
B_LOG=/tmp/sp_meshB.log
rm -f "$A_LOG" "$B_LOG"

python3 harness/meshd.py --name alice --port 5001 \
  --publish $'reg/STRAT/list:fns:DELTA:qwen2.5-coder:7b\t1' \
  --publish $'reg/STRAT/count:lines:TARGETED:qwen2.5-coder:1.5b\t1' \
  --hash-interval 0.5 > "$A_LOG" 2>&1 &
APID=$!

python3 harness/meshd.py --name bob --port 5002 \
  --peer 127.0.0.1:5001 \
  --publish $'reg/STRAT/lint:remove:DELTA:qwen2.5-coder:1.5b\t0' \
  --publish $'reg/STRAT/rename:tmp:DELTA:qwen2.5-coder:1.5b\t1' \
  --hash-interval 0.5 > "$B_LOG" 2>&1 &
BPID=$!

sleep 4
kill -INT "$APID" "$BPID" 2>/dev/null
wait "$APID" "$BPID" 2>/dev/null

A=$(grep -oP 'HASH \K[0-9a-f]+' "$A_LOG" | tail -1)
B=$(grep -oP 'HASH \K[0-9a-f]+' "$B_LOG" | tail -1)
TAIL_A=$(grep -oP 'tail=\K[0-9]+' "$A_LOG" | tail -1)
TAIL_B=$(grep -oP 'tail=\K[0-9]+' "$B_LOG" | tail -1)

echo "A hash: $A (tail=$TAIL_A)"
echo "B hash: $B (tail=$TAIL_B)"

if [ -z "$A" ] || [ -z "$B" ] || [ "$A" != "$B" ]; then
  echo "FAIL: nodes did not converge"
  exit 1
fi
if [ "$TAIL_A" -lt 4 ] || [ "$TAIL_B" -lt 4 ]; then
  echo "FAIL: expected >=4 total ops folded (2 each way), got A=$TAIL_A B=$TAIL_B"
  exit 1
fi
echo "PASS: two-node mesh converged ($TAIL_A ops, shared hash)"
