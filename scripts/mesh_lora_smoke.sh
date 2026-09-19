#!/usr/bin/env bash
# mesh_lora_smoke.sh -- registry learning over a simulated LoRa radio.
#
# Node A publishes a batch of registry ops as ONE lora_share message;
# the radio chunks it into narrowband frames (lossy, half-duplex,
# ~96B payloads) with a stop-and-wait ARQ; node B reassembles and
# applies.  Both nodes must converge to the same state hash.
set -u
cd "$(dirname "$0")/.."
export STATEPOD_LIB=$PWD/libstatepod.so

echo "=== two mesh nodes over a lossy LoRa radio (20% frame loss) ==="
timeout 25 python3 scripts/mesh_lora.py --name node-a --role a \
  --publish $'reg/STRAT/WRITE:DELTA\t1' \
  --publish $'reg/STRAT/WRITE:FULL\t1' \
  --publish $'reg/MODEL:q:abc:7b\t1' \
  --loss 0.2 --airtime 0.02 --seed 5 2>&1 | tee /tmp/sp_lora_smoke.log \
  | grep -E "HASH|B-HASH|stats" | tail -8

echo ""
echo "=== convergence check ==="
python3 - <<'EOF'
import re
lines = open("/tmp/sp_lora_smoke.log").read()
a_hashes = re.findall(r"HASH (\w+)  tail=(\d+)", lines)
b_hashes = re.findall(r"B-HASH (\w+)", lines)
a_tail3 = {h for h, t in a_hashes if int(t) == 3}
b_tail3 = set(b_hashes[-1:]) if b_hashes else set()
print("  node A tail=3:", sorted(a_tail3))
print("  node B final :", sorted(b_tail3))
shared = a_tail3 & b_tail3
if shared:
    print(f"  PASS: both nodes converged through the lossy radio "
          f"(shared hash {sorted(shared)[0][:12]}...)")
else:
    print("  FAIL: node B did not converge")
    raise SystemExit(1)
EOF
