#!/usr/bin/env bash
# broker_capability_demo.sh -- capability_announce + broker routing.
#
# Providers announce what they can serve (models, load, freshness).
# A consumer's broker scores them (model match, load, staleness) and
# DIRECTS the inference request to the best node; if the picked node
# stalls or everyone is loaded, it falls back to broadcast, and the
# orchestrator falls back to local inference.
set -u
cd "$(dirname "$0")/.."
export STATEPOD_LIB=$PWD/libstatepod.so
export SP_MESH_TIMEOUT=6   # fast fallback in the demo (default 20s)

pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== providers announce capabilities ==="
python3 harness/meshd.py --name prov-a --port 5901 --serve-model \
  qwen2.5-coder:1.5b --serve-backend mock \
  --announce-models qwen2.5-coder:1.5b --hash-interval 3 \
  > /tmp/sp_bcap_a.log 2>&1 &
PA=$!
python3 harness/meshd.py --name prov-b --port 5902 --serve-model \
  qwen2.5-coder:7b --serve-backend mock \
  --announce-models qwen2.5-coder:7b --hash-interval 3 \
  > /tmp/sp_bcap_b.log 2>&1 &
PB=$!
sleep 2

echo ""
echo "=== ACT 1: both idle -> broker routes 7b task to prov-b ==="
python3 scripts/run_named.py --backend mesh --names "count src/math.c" \
  --model qwen2.5-coder:7b --no-escalate \
  --mesh-name consumer --mesh-port 5903 \
  --mesh-peer 127.0.0.1:5901 --mesh-peer 127.0.0.1:5902 \
  --results /tmp/sp_bcap_r1.jsonl 2>&1 | grep -E "ok$|FAIL"
grep "answered" /tmp/sp_bcap_b.log | tail -1 | sed 's/^/   prov-b: /'

echo ""
echo "=== ACT 2: prov-b (7b) is LOADED -> broker routes to prov-a ==="
kill -STOP $PB   # freeze prov-b: simulate a wedged busy node
sleep 3
python3 scripts/run_named.py --backend mesh --names "count src/math.c" \
  --model qwen2.5-coder:7b --no-escalate \
  --mesh-name consumer --mesh-port 5903 \
  --mesh-peer 127.0.0.1:5901 --mesh-peer 127.0.0.1:5902 \
  --results /tmp/sp_bcap_r2.jsonl 2>&1 | grep -E "ok$|FAIL"
grep "answered" /tmp/sp_bcap_a.log | tail -1 | sed 's/^/   prov-a: /'
kill -CONT $PB

echo ""
echo "=== ACT 3: no providers -> local fallback ==="
kill -INT $PA $PB 2>/dev/null; sleep 1
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1
python3 scripts/run_named.py --backend mesh --names "count src/math.c" \
  --model qwen2.5-coder:1.5b --no-escalate \
  --mesh-name consumer --mesh-port 5903 \
  --results /tmp/sp_bcap_r3.jsonl --show-turns 2>&1 \
  | grep -E "ok$|FAIL|fallback|mesh planner" | head -3

echo ""
echo "=== broker view (consumer side) ==="
python3 - <<'EOF'
import re
for f, name in [("/tmp/sp_bcap_a.log", "prov-a"), ("/tmp/sp_bcap_b.log", "prov-b")]:
    for line in open(f):
        if "answered" in line:
            m = re.search(r"answered (\S+) ok=(\w+) model=(\S+) load=([\d.]+)", line)
            if m:
                print(f"  {name}: req={m.group(1)} ok={m.group(2)} model={m.group(3)} load={m.group(4)}")
EOF
