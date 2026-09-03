#!/usr/bin/env bash
# mesh_broker_demo.sh -- mesh:// inference broker (spec v2 6.1).
#
# One node PROVIDES inference (a local model); a CONSUMER node asks the
# mesh "who can plan this task?" instead of planning itself.  The
# consumer executes the provider's plan locally.  Second act: kill the
# provider and show the consumer falling back to its own model.
set -u
cd "$(dirname "$0")/.."
export SWARMSTATE_LIB=$PWD/libswarmstate.so

R=/tmp/ss_broker_demo.jsonl
rm -f "$R"
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== ACT 1: provider serves inference over the mesh ==="
python3 harness/meshd.py --name provider --port 5701 < /dev/null \
  --serve-model qwen2.5-coder:1.5b --hash-interval 3 \
  > /tmp/ss_broker_provider.log 2>&1 &
PROV_PID=$!
sleep 2

python3 scripts/run_named.py --backend mesh \
  --names "count src/math.c,grep TODO,rename tmp->buffer@src/util.c" \
  --results "$R" \
  --mesh-name consumer --mesh-port 5702 --mesh-peer 127.0.0.1:5701 \
  2>&1 | grep -E "ok$|WEAK|FAIL"

echo ""
echo "provider log:"
grep "answered" /tmp/ss_broker_provider.log | head -3

echo ""
echo "=== ACT 2: provider dies -> consumer falls back to local model ==="
kill -INT $PROV_PID 2>/dev/null; sleep 1
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

python3 scripts/run_named.py --backend mesh \
  --names "count src/math.c" --results /tmp/ss_broker_fb.jsonl \
  --model qwen2.5-coder:1.5b \
  --mesh-name consumer --mesh-port 5702 2>&1 | grep -E "ok$|WEAK|FAIL|fallback"

echo ""
echo "=== verdicts ==="
python3 - <<'EOF'
import json
for l in open("/tmp/ss_broker_demo.jsonl"):
    r = json.loads(l)
    print(f"  {r.get('task','?'):30} ok={r.get('ok')} sem={r.get('ok_semantic')}")
EOF
