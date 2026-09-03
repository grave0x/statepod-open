#!/usr/bin/env bash
# multi_homing_demo.sh -- federation multi-homing (spec addition).
#
# One farm node holds THREE memberships: its own pool, the Landmark
# client pool (bridge: WRITE strategy learning only), and the CFS
# emergency pool (bridge: MODEL routing learning only).
#   - learning crosses BRIDGES SOURCE-TAGGED (reg/POOL/<src>/...), so
#     it never lands in a receiving pool's local partition
#   - two bridges share one node with independent policies + ledgers
#   - a cross-boundary high-risk action needs multi-party approval
#     (Landmark fleet manager + CFS incident controller)
set -u
cd "$(dirname "$0")/.."
pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== federation multi-homing: one node, three pools, two bridges ==="
python3 - <<'EOF'
import sys, time, json
sys.path.insert(0, "harness")
from meshd import MeshDaemon
from bridge import BridgePolicy, PoolBridge
from pool import issue_invite
from governance import Governance, issue_token

def node(name, port, hub, secret, pool):
    inv = issue_invite(secret, pool, f"127.0.0.1:{hub}", ttl=600)
    return MeshDaemon(name, port, [("127.0.0.1", hub)], allow=set(),
                      join_invite=inv)

ha = MeshDaemon("hub-farm", 6170, [], allow={"hub-farm"},
                pool_secret="SA", pool_name="farm")
hb = MeshDaemon("hub-landmark", 6175, [], allow={"hub-landmark"},
                pool_secret="SB", pool_name="landmark")
hc = MeshDaemon("hub-cfs", 6180, [], allow={"hub-cfs"},
                pool_secret="SC", pool_name="cfs")
fa = node("field-farm", 6171, 6170, "SA", "farm")
fb = node("field-landmark", 6176, 6175, "SB", "landmark")
fc = node("field-cfs", 6181, 6180, "SC", "cfs")
ba = node("bridge-a", 6172, 6170, "SA", "farm")
bb = node("bridge-b", 6177, 6175, "SB", "landmark")
bc = node("bridge-c", 6182, 6180, "SC", "cfs")
for d in (ha, fa, hb, fb, hc, fc, ba, bb, bc): d.start()
time.sleep(3.0)
for d in (ba, bb, bc):
    assert d._join_done.wait(3), f"{d.name} must join"

print("  farm node joined 3 pools: farm / landmark / cfs (separate identities)")
b1 = PoolBridge("farm-landmark", ba, bb,
                BridgePolicy(allow_targets=("reg/STRAT/WRITE/*",), ttl=30))
b2 = PoolBridge("farm-cfs", ba, bc,
                BridgePolicy(allow_targets=("reg/MODEL/*",), ttl=30))
time.sleep(0.5)

fa.publish("reg/STRAT/WRITE/DELTA", "1")      # b1 allows
fa.publish("reg/STRAT/WRITE/DELTA", "1")
fa.publish("reg/MODEL/q:rescue/7b", "1")      # b2 allows
time.sleep(2.5)

sb = json.loads(fb.peer.state_json())
sc = json.loads(fc.peer.state_json())
lm_learn = sb.get("reg", {}).get("POOL", {}).get("farm", {})
cf_learn = sc.get("reg", {}).get("POOL", {}).get("farm", {})
print("  landmark sees farm WRITE learning:",
      "DELTA" in lm_learn.get("STRAT", {}).get("WRITE", {}))
print("  landmark sees farm MODEL keys:      ",
      "MODEL" not in lm_learn)
print("  cfs sees farm MODEL learning:      ",
      "7b" in cf_learn.get("MODEL", {}).get("q:rescue", {}))
print("  cfs sees farm WRITE learning:      ",
      "STRAT" not in cf_learn)
print("  local partitions stay clean:       ",
      "STRAT" not in sb.get("reg", {}) and "MODEL" not in sc.get("reg", {}))
print("  bridge ledgers independent:        ",
      f"farm-landmark {b1.relayed}/{b1.denied}, "
      f"farm-cfs {b2.relayed}/{b2.denied}")

print("")
print("=== multi-party governance: CONTROL_VEHICLE needs 2 authorities ===")
secrets = {"landmark": "SB", "cfs": "SC"}
gov = Governance(mode="enforce")
t_lm = issue_token("SB", role="fleet_manager", ttl=300)
t_cf = issue_token("SC", role="incident_controller", ttl=300)
entry = gov.gate_multi({"landmark": t_lm, "cfs": t_cf}, secrets,
                       {"landmark", "cfs"},
                       plan_sig="CONTROL_VEHICLE:limit_speed",
                       summary="flood waters ahead, driver unresponsive")
print(f"  both tokens valid   -> {entry['verdict']} "
      f"({entry['detail']})")
denied = gov.gate_multi({"landmark": t_lm}, secrets, {"landmark", "cfs"},
                        plan_sig="CONTROL_VEHICLE:limit_speed")
print(f"  only fleet manager  -> {denied['verdict']} "
      f"({denied['detail']})")

for d in (ha, fa, hb, fb, hc, fc, ba, bb, bc): d.shutdown()
print("")
print("  multi-homing demo PASS")
EOF
