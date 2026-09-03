#!/usr/bin/env bash
# pool_bridge_demo.sh -- scoped bridge between two pools (spec v1 s5).
#
# Pool A (brigade) and pool B (hospital) each run their own hub with
# their own secret.  A bridge joins BOTH pools and carries only what
# the policy allows:
#   - registry STRAT learning for WRITE signatures crosses
#   - MODEL routing keys stay local (data minimization)
#   - other signatures are refused (task-signature scope)
#   - the bridge expires after a time window (ttl)
set -u
cd "$(dirname "$0")/.."

pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== two pools, one scoped bridge ==="
python3 - <<'EOF'
import sys, time, json
sys.path.insert(0, "harness")
from meshd import MeshDaemon
from pool import issue_invite
from bridge import BridgePolicy, PoolBridge

SA, SB = "secret-A", "secret-B"
inv_a = issue_invite(SA, "cfs-brigade-3", "127.0.0.1:6040", ttl=600)
inv_b = issue_invite(SB, "ward-7", "127.0.0.1:6045", ttl=600)
ha = MeshDaemon("hub-a", 6040, [], allow={"hub-a"}, pool_secret=SA, pool_name="cfs-brigade-3")
fa = MeshDaemon("field-a", 6041, [("127.0.0.1", 6040)], allow=set(), join_invite=inv_a)
hb = MeshDaemon("hub-b", 6045, [], allow={"hub-b"}, pool_secret=SB, pool_name="ward-7")
fb = MeshDaemon("field-b", 6046, [("127.0.0.1", 6045)], allow=set(), join_invite=inv_b)
ba = MeshDaemon("bridge-a", 6042, [("127.0.0.1", 6040)], allow=set(), join_invite=inv_a)
bb = MeshDaemon("bridge-b", 6047, [("127.0.0.1", 6045)], allow=set(), join_invite=inv_b)
for d in (ha, fa, hb, fb, ba, bb): d.start()
time.sleep(3.0)
for d in (ba, bb):
    assert d._join_done.wait(3), f"{d.name} must join its pool"

print("  bridge sides joined both pools (mutual acceptance)")
print("  policy: allow_targets=reg/STRAT/WRITE:*  ttl=20s  minimize=on")
br = PoolBridge("brigade-ward", ba, bb,
                BridgePolicy(allow_targets=("reg/STRAT/WRITE:*",), ttl=20))
time.sleep(0.5)

fa.publish("reg/STRAT/WRITE:DELTA", "1")     # bridge ok
time.sleep(1.5)
b1 = json.loads(fb.peer.state_json())
print("  brigade WRITE lesson -> ward registry:",
      "WRITE:DELTA" in b1.get("reg", {}).get("STRAT", {}))

fa.publish("reg/MODEL:q:brigade:7b", "1")     # bridge denies
time.sleep(1.5)
b2 = json.loads(fb.peer.state_json())
print("  brigade MODEL key    -> ward registry:",
      "MODEL" not in b2.get("reg", {}))

fa.publish("reg/STRAT/READ:TARGETED", "1")    # bridge denies
time.sleep(1.5)
b3 = json.loads(fb.peer.state_json())
print("  brigade READ lesson  -> ward registry:",
      "READ:TARGETED" not in b3.get("reg", {}).get("STRAT", {}))

print("")
print("=== bridge ledger ===")
for e in br.policy.log_lines:
    print(f"  {e['verdict']:5} {e['target']:28} ({e['why']})")
print(f"  relayed={br.relayed} denied={br.denied}")
for d in (ha, fa, hb, fb, ba, bb): d.shutdown()
EOF
