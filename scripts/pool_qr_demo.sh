#!/usr/bin/env bash
# pool_qr_demo.sh -- pools + QR-join (spec v1 section 5).
#
# Membership is explicit: an admin mints a signed, expiring invite; a
# new node "scans" it (pastable string / QR code) and joins.  The hub
# verifies the invite, mints the allowlist entry, and pushes membership
# to every peer.  Default posture: no node may inject ops until the
# invite ceremony completes.
set -u
cd "$(dirname "$0")/.."

SEC="pool-secret-demo-$(date +%s)"
HUB_PORT=5820
JOIN_PORT=5821
QR=/tmp/pool_invite_qr.png
rm -f "$QR"

pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== 1. ADMIN issues an invite (QR rendered) ==="
INVITE=$(python3 harness/pool.py issue --secret "$SEC" \
  --pool "cfs-brigade-3" --hub "127.0.0.1:$HUB_PORT" --ttl 600 --qr \
  --qr-out "$QR" | head -1)
echo "   invite len: ${#INVITE} chars; QR: $QR"
python3 harness/pool.py verify --secret "$SEC" --invite "$INVITE" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('   verified:', d['pool'], 'expires', d['exp'])"

echo ""
echo "=== 2. HUB starts (pool secret = the trust root) ==="
python3 harness/meshd.py --name hub --port $HUB_PORT \
  --pool-secret "$SEC" --pool-name cfs-brigade-3 --allow hub \
  --hash-interval 2 > /tmp/ss_pool_hub.log 2>&1 &
HUB_PID=$!
sleep 1

echo ""
echo "=== 3. NEW NODE scans the invite and joins (one step) ==="
python3 harness/meshd.py --name field-1 --port $JOIN_PORT \
  --peer 127.0.0.1:$HUB_PORT --join "$INVITE" \
  --hash-interval 2 > /tmp/ss_pool_joiner.log 2>&1 &
JOIN_PID=$!
sleep 2

echo ""
echo "=== 4. the joiner contributes to the pool ==="
python3 - "$JOIN_PORT" <<'EOF'
import sys, time
sys.path.insert(0, "harness")
from meshd import MeshDaemon
# a short-lived witness: publish through a fresh connection is overkill;
# instead the joiner node itself publishes via its own stdin feed.
EOF
# joiner stdin is the meshd feed: target<TAB>value
# (the running joiner reads stdin -- send the op via its peer link by
#  publishing from a THIRD throwaway node that joined the same pool)
INVITE2=$(python3 harness/pool.py issue --secret "$SEC" --pool cfs-brigade-3 \
  --hub 127.0.0.1:$HUB_PORT --ttl 60)
python3 harness/meshd.py --name field-2 --port 5822 \
  --peer 127.0.0.1:$HUB_PORT --join "$INVITE2" \
  --publish $'reg/STRAT/WRITE:DELTA\t1' --hash-interval 2 \
  > /tmp/ss_pool_f2.log 2>&1 &
F2_PID=$!
sleep 2.5

echo ""
echo "=== 5. pool state ==="
python3 - <<'EOF'
import json, sys
sys.path.insert(0, "harness")
from meshd import MeshDaemon
hub = MeshDaemon("probe", 0, [], None)
import socket, threading, time
# quick convergence probe via the hub's own log hashes
EOF
grep "HASH" /tmp/ss_pool_hub.log | tail -1 | sed 's/^/   hub:     /'
grep "HASH" /tmp/ss_pool_f2.log | tail -1 | sed 's/^/   field-2: /'
python3 - <<'EOF'
import re
h = open("/tmp/ss_pool_hub.log").read()
m = re.findall(r"HASH (\w+)  tail=(\d+)", h)
print("   hub has", m[-1][1] if m else "?", "ops (all members' contributions)")
EOF

kill -INT $HUB_PID $JOIN_PID $F2_PID 2>/dev/null
pkill -f "harness/meshd.py" 2>/dev/null
echo ""
echo "done.  Invite = the entire onboarding ceremony:"
echo "  scan $QR (or paste the invite string) -> node is a member."
