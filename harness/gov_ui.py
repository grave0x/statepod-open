"""GovUI: governance approval workflow server.
Serves the 04-gov-approve.html mockup and exposes JSON endpoints wired
into harness/governance.py (issue_token, verify_token, check_multiparty).
No external deps: stdlib http.server only.
"""
from __future__ import annotations
import argparse, base64, hashlib, hmac, json, os, sys, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

from governance import Governance, issue_token, verify_token, check_multiparty

DEFAULT_PORT = int(os.environ.get("SP_GOV_UI_PORT", "8080"))
# C4: no hardcoded default — refuse start without explicit secret
DEFAULT_SECRET = os.environ.get("SP_GOV_SECRET") or None
DEFAULT_TTL = 300
DEFAULT_REQUIRED = frozenset({"supervisor", "field_lead"})

_pending = {}
_audit = []

def _now(): return time.time()

def _sign(op_id, reason, secret):
    line = f"{_now():.6f} | {op_id} | {reason}"
    h = hashlib.sha256(line.encode()).hexdigest()
    return f"{line} | hash={h[:16]}"

class GovHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._serve_page()
        elif p == "/health":
            self.send_json({"status": "ok", "pending": len(_pending), "audit": len(_audit)})
        else:
            self.send_error(404)

    def _serve_page(self):
        html = _HARNESS.parent / "docs" / "ux" / "04-gov-approve.html"
        body = html.read_bytes() if html.exists() else self._fallback().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fallback(self):
        return """<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#111;padding:24px;font-family:system-ui}
.card{background:#00005f;border-radius:8px;padding:16px;max-width:520px}
h1{color:#55ffff;margin:0 0 12px}button{padding:8px 16px;border:none;
border-radius:6px;cursor:pointer;font-size:14px;margin:4px}
#approve{background:#00af00;color:#fff}#deny{background:#5f0000;color:#fff}
pre{background:#000;padding:12px;border-radius:6px;font-size:12px;
color:#0f0;max-height:200px;overflow:auto}</style></head><body>
<div class="card"><h1>StatePod Governance Approval</h1>
<p id="msg">Loading…</p>
<button id="issue">Issue token (supervisor)</button>
<button id="approve">Approve + stamp</button>
<button id="deny">Deny</button>
<pre id="out">-- audit log --</pre></div>
<script>
function log(m){var o=document.getElementById("out");
o.textContent+=m+"\n";o.scrollTop=o.scrollHeight}
document.getElementById("issue").onclick=async function(){
  var r=await fetch("/api/gov/issue-token",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({role:"supervisor"})});
  var d=await r.json();log("ISSUE: "+JSON.stringify(d))};
document.getElementById("approve").onclick=async function(){
  var r=await fetch("/api/gov/approve",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({op_id:"op-001",reason:"Approved by supervisor"})});
  var d=await r.json();log("APPROVE: "+JSON.stringify(d))};
document.getElementById("deny").onclick=async function(){
  var r=await fetch("/api/gov/deny",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({op_id:"op-001",reason:"Denied"})});
  var d=await r.json();log("DENY: "+JSON.stringify(d))};
log("server ready");
</script></body></html>"""

    def do_POST(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            data = {}
        if p == "/api/gov/issue-token":
            self._issue(data)
        elif p == "/api/gov/approve":
            self._approve(data)
        elif p == "/api/gov/deny":
            self._deny(data)
        else:
            self.send_error(404)

    def _issue(self, d):
        role = d.get("role", "supervisor")
        ttl = int(d.get("ttl", DEFAULT_TTL))
        try:
            tok = issue_token(args.secret, role, ttl)
            self.send_json({"ok": True, "token": tok, "role": role, "ttl": ttl})
        except ValueError as e:
            self.send_json({"ok": False, "error": str(e)}, 400)

    def _approve(self, d):
        op_id = d.get("op_id", "op-001")
        reason = d.get("reason", "")
        tokens = d.get("tokens", {})
        required = set(d.get("required", DEFAULT_REQUIRED))
        summary = d.get("summary", op_id)
        plan_sig = d.get("plan_sig")
        secret = args.secret
        g = Governance(mode="enforce", governed_ops={"EXECUTE", "WRITE"}, secret=secret)
        if tokens and required:
            entry = g.gate_multi(tokens, {k: secret for k in tokens}, required,
                                 plan_sig=plan_sig, summary=summary)
        else:
            entry = g._check_op({"type": "EXECUTE", "summary": op_id, "reason": reason}, plan_sig)
        entry.update({"ts": _now(), "op_id": op_id, "reason": reason, "journal": _sign(op_id, reason, secret)})
        _audit.append(entry)
        _pending[op_id] = entry
        self.send_json({"ok": True, "verdict": entry["verdict"],
                        "detail": entry.get("detail",""), "journal": entry["journal"],
                        "missing": entry.get("missing", [])})

    def _deny(self, d):
        op_id = d.get("op_id", "op-001")
        reason = d.get("reason", "Denied by operator")
        entry = {"ts": _now(), "op_id": op_id, "op": "DENY", "summary": reason,
                 "verdict": "DENY", "authorizer": "human", "reason": reason,
                 "journal": _sign(op_id, reason, args.secret)}
        _audit.append(entry)
        _pending[op_id] = entry
        self.send_json({"ok": True, "verdict": "DENY", "reason": reason,
                        "journal": entry["journal"]})

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args): pass


def main():
        # global args  # refactored: avoid global
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--secret", default=DEFAULT_SECRET,
                    help="HMAC secret (or SP_GOV_SECRET); required")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if not args.secret:
        sys.exit("gov_ui: refuse start without --secret or SP_GOV_SECRET")
    print(f"[gov_ui] Serving on http://{args.host}:{args.port}")
    print(f"[gov_ui] Secret set: {bool(args.secret)}")
    print(f"[gov_ui] Two-person rule roles: {DEFAULT_REQUIRED}")
    print(f"[gov_ui] UI: http://{args.host}:{args.port}/  |  Health: .../health")
    HTTPServer((args.host, args.port), GovHandler).serve_forever()

if __name__ == "__main__":
    main()
