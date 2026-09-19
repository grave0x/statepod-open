#!/usr/bin/env bash
# acceptance_demo.sh -- the spec v1 section 9 acceptance run.
#
# One script, four criteria, every one proven by a real demo:
#   1. STATE SYNC         pool_qr_demo       (QR-joined pool converges)
#   2. LOCAL INFERENCE    broker_capability_demo  (mesh broker routes)
#   3. CROSS-NODE LEARN   mesh_flip_demo     (Node B refuses bad lesson)
#   4. GOVERNANCE         governance_demo    (stop button, audited)
#   +  AIR-GAPPED PATH    mesh_lora_smoke    (lossy radio, ARQ, converges)
#
# Run it in front of anyone and read the verdict table at the end.
set -u
cd "$(dirname "$0")/.."
export STATEPOD_LIB=$PWD/libstatepod.so

RESULTS=/tmp/sp_acceptance
mkdir -p "$RESULTS"
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

PASS=0; FAIL=0
check() {  # check <name> <exit_code>
  if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "  [PASS] $1";
  else FAIL=$((FAIL+1)); echo "  [FAIL] $1"; fi
}

echo "=== 1/5 STATE SYNC -- QR-joined pool converges ==="
bash scripts/pool_qr_demo.sh > "$RESULTS/pool.log" 2>&1
check "pool_qr_demo (invite ceremony + convergence)" $?

echo ""
echo "=== 2/5 LOCAL INFERENCE -- broker routes to the right provider ==="
bash scripts/broker_capability_demo.sh > "$RESULTS/broker.log" 2>&1
check "broker_capability_demo (route + fallback)" $?

echo ""
echo "=== 3/5 CROSS-NODE LEARNING -- Node B refuses Node A's bad lesson ==="
bash scripts/mesh_flip_demo.sh > "$RESULTS/flip.log" 2>&1
check "mesh_flip_demo (4/4 strategy flip)" $?

echo ""
echo "=== 4/5 GOVERNANCE -- the stop button ==="
bash scripts/governance_demo.sh > "$RESULTS/gov.log" 2>&1
check "governance_demo (deny/approve/expire/human)" $?

echo ""
echo "=== 5/5 AIR-GAPPED -- lossy LoRa radio converges ==="
bash scripts/mesh_lora_smoke.sh > "$RESULTS/lora.log" 2>&1
check "mesh_lora_smoke (radio + ARQ)" $?

echo ""
echo "==============================================================="
echo "  SPEC v1 SECTION 9 ACCEPTANCE:  $PASS/5 criteria proven"
echo "==============================================================="
echo "  details: $RESULTS/*.log"
exit $((FAIL > 0))
