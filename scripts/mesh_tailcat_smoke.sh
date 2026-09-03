#!/usr/bin/env bash
# mesh_tailcat_smoke.sh -- SwarmState mesh over a Tailcat tunnel (Phase 2).
#
# Two nodes exchange registry STRAT ops through a WireGuard-encrypted
# Tailcat tunnel (DERP rendezvous) and converge on one shared state hash.
# Requires tailcat: go install github.com/tailscale/tailcat/cmd/tailcat@latest
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

command -v tailcat >/dev/null || { echo "SKIP: tailcat not installed"; exit 0; }

A_LOG=/tmp/ss_tcA.log
B_LOG=/tmp/ss_tcB.log
rm -f "$A_LOG" "$B_LOG"
pkill -x tailcat 2>/dev/null
sleep 1

python3 scripts/mesh_tailcat.py server --name alice --port 7101 \
  --publish $'reg/STRAT/a:one:DELTA\t1' > "$A_LOG" 2>&1 &
APID=$!
sleep 10
TOKEN=$(grep -oP 'tc[A-Za-z0-9_-]+' "$A_LOG" | tail -1)
if [ -z "$TOKEN" ]; then
  echo "FAIL: no Tailcat token (network/DERP unreachable?)"
  kill "$APID" 2>/dev/null; exit 1
fi

python3 scripts/mesh_tailcat.py client --name bob --token "$TOKEN" --port 7101 \
  --publish $'reg/STRAT/b:two:DELTA\t1' > "$B_LOG" 2>&1 &
BPID=$!
sleep 14

kill -INT "$APID" "$BPID" 2>/dev/null
sleep 1
pkill -x tailcat 2>/dev/null

TA=$(grep -oP 'tail=\K[0-9]+' "$A_LOG" | sort -n | tail -1)
TB=$(grep -oP 'tail=\K[0-9]+' "$B_LOG" | sort -n | tail -1)
HA=$(grep -oP 'HASH \K[0-9a-f]+' "$A_LOG" | sort -u | grep -v '^44136fa' | tail -1)
HB=$(grep -oP 'HASH \K[0-9a-f]+' "$B_LOG" | sort -u | grep -v '^44136fa' | tail -1)

echo "A tail=$TA  B tail=$TB"
if [ "$TA" -lt 2 ] || [ "$TB" -lt 2 ]; then
  echo "FAIL: expected >=2 ops folded on each side (got A=$TA B=$TB)"
  exit 1
fi
if [ -z "$HA" ] || [ -z "$HB" ] || [ "$HA" != "$HB" ]; then
  echo "FAIL: hashes differ (A=$HA B=$HB)"
  exit 1
fi
echo "PASS: two-node mesh converged over Tailcat (hash ${HA:0:16}...)"
