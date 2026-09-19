#!/usr/bin/env bash
# governance_demo.sh -- the stop button (spec v1 section 6).
#
# Four acts on the same governed task (EXECUTE):
#   1. enforce, no token        -> DENIED, audited
#   2. enforce, valid token     -> APPROVED, executes, audited
#   3. enforce, expired token   -> DENIED (stale credentials), audited
#   4. interactive, human says  -> y: executes (HUMAN_APPROVE)
#                                 n: refused (HUMAN_DENY)
# Every decision lands in the audit ledger. The model can never get a
# governed op past the gate without auth + reason. No exceptions.
set -u
cd "$(dirname "$0")/.."
export STATEPOD_LIB=$PWD/libstatepod.so

SEC="demo-secret-$(date +%s)"
RES=/tmp/sp_gov_demo.jsonl
LOG=/tmp/sp_gov_demo.governance.log
rm -f "$RES" "$LOG"

# a valid, short-lived supervisor token (HMAC-signed, role-scoped)
TOK=$(python3 - "$SEC" <<'EOF'
import sys
sys.path.insert(0, "harness")
from governance import issue_token
print(issue_token(sys.argv[1], ttl=300))
EOF
)
EXPIRED=$(python3 - "$SEC" <<'EOF'
import sys
sys.path.insert(0, "harness")
from governance import issue_token
print(issue_token(sys.argv[1], ttl=-60))
EOF
)

GOV="--governance enforce --governed-op EXECUTE --supervisor-secret $SEC --governance-log $LOG --results $RES"
run() {
  python3 scripts/run_named.py --backend mock --names "count src/math.c" \
    $GOV "$@" 2>&1 | grep -E "ok$|FAIL|WEAK|governance|DENY" | head -2
}

echo "=== ACT 1: enforce, NO token -> the gate refuses ==="
run
echo ""
echo "=== ACT 2: enforce, valid supervisor token -> approved ==="
run --mock-auth-token "$TOK"
echo ""
echo "=== ACT 3: enforce, EXPIRED token -> refused (stale) ==="
run --mock-auth-token "$EXPIRED"
echo ""
echo "=== ACT 4a: interactive, human approves (y) ==="
echo "y" | python3 scripts/run_named.py --backend mock --names "count src/math.c" \
  --governance interactive --governed-op EXECUTE --supervisor-secret "$SEC" \
  --governance-log "$LOG" --results "$RES" 2>&1 | grep -E "ok$|FAIL|governance" | head -2
echo ""
echo "=== ACT 4b: interactive, human refuses (n) ==="
echo "n" | python3 scripts/run_named.py --backend mock --names "count src/math.c" \
  --governance interactive --governed-op EXECUTE --supervisor-secret "$SEC" \
  --governance-log "$LOG" --results "$RES" 2>&1 | grep -E "ok$|FAIL|governance" | head -2

echo ""
echo "=== AUDIT LEDGER ($LOG) ==="
python3 - <<'EOF'
import json
for l in open("/tmp/sp_gov_demo.governance.log"):
    e = json.loads(l)
    print(f"  {e['verdict']:13} {e['op']:8} {e['summary']:20} "
          f"auth={e['auth_present']} reason={e['reason_present']}"
          + (f" role={e['role']}" if 'role' in e else ""))
EOF
