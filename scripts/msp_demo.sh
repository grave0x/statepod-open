#!/usr/bin/env bash
# MSP 10-min demo checklist (build-plan phase 5 / docs/ux/00-msp-flow.uxd)
# join → diagnose → remediate. Prints steps; does not open windows.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
WEB="${SS_DEMO_WEB:-18080}"
MESH="${SS_DEMO_MESH:-17700}"

cat <<EOF
=== SwarmState MSP demo (scripted) ===
Wireframes: docs/ux/00-msp-flow.uxd (+ role screens)
Security:   docs/phase4-security-gate.md (Strix still open)

1. JOIN
   Terminal A:  ./win/build/swarmstate-node-linux --name hub --port $MESH --web $WEB --root /tmp/ss_demo_hub
   Terminal B:  ./win/build/swarmstate-node-linux --name field --peer 127.0.0.1:$MESH --web $((WEB+1)) --root /tmp/ss_demo_field
   Browser:     http://127.0.0.1:$WEB  → role=admin → QR widget → copy invite
   (open HTML on an empty Hyprland workspace — do not steal focus)

2. DIAGNOSE
   curl -s http://127.0.0.1:$WEB/api/state | python3 -m json.tool | head
   Watch alerts: SS_WATCH_INTERVAL_MS=5000 on the node
   Tasks/STRAT:  bin/swarmcli stats   (from a repo with registry history)

3. REMEDIATE (copy-token gov on the node)
   Start hub with: --gov-secret demo-secret
   Browser role=supervisor → Governance → Mint token → Approve
   Or API:  curl -s localhost:$WEB/api/gov/issue
            curl -s -X POST localhost:$WEB/api/gov/decide \
              -H 'Content-Type: application/json' \
              -d '{"verdict":"APPROVE","token":"…","reason":"demo"}'
            # GET query still works for quick demos
   Harness (same secret): bin/swarmcli gov verify --token "\$TOK" --secret demo-secret
   # D2: stop peer B → watch forces reconnect when peer returns
   # POST /api/gov/decide JSON body supported (GET query still works)

Ctrl+C nodes when done. Sandbox roots under /tmp/ss_demo_*.
EOF
mkdir -p /tmp/ss_demo_hub /tmp/ss_demo_field
echo "(sandbox dirs ready)"
