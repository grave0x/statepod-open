"""Governance: the authorization layer above the mechanical kernel.

Spec (common-domain-adaptation-spec-v1.md, §6):
  For any op that can harm people, equipment, or data, enforce:
    - schema validation   (the op matches a predefined shape)
    - auth token          (signed by a supervisor or role with authority)
    - recorded reason     (human-readable justification in the event log)
    - staged response     (alert first, then limits, then full stop)
    - human override      (always possible, always logged)

Layering (defence in depth):
  1. kernel (C)        -- mechanical: op schema, path containment,
                          binary allowlist, no shell metacharacters.
  2. governance (this) -- authorization: governed ops need a valid
                          supervisor token AND a recorded reason.
  3. human override    -- interactive mode prompts the operator.

Risk tiers (spec §3): SAFE (read-only), WRITE, EXEC, ACTUATE (reserved
for future CONTROL_* ops).  Governed ops are a deployment choice: pass
governed_ops={"EXECUTE"} to require authorization for every execute.

Tokens: HMAC-SHA256-signed, short-lived, role-scoped.
  payload = b"ss:<role>:<expiry-epoch>"
  token   = base64url(payload + "." + hmac(secret, payload))
Verification is constant-time and refuses expired tokens.  A token is
NOT a capability by itself: the op must ALSO carry a reason.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

# ── risk tiers ────────────────────────────────────────────────────────
TIER_SAFE = "SAFE"
TIER_WRITE = "WRITE"
TIER_EXEC = "EXEC"
TIER_ACTUATE = "ACTUATE"

# op type -> tier.  Anything not listed is SAFE by default.
_OP_TIERS = {
    "EXECUTE": TIER_EXEC,
    "WRITE": TIER_WRITE,
    "APPEND": TIER_WRITE,
    "CONFIG_UPDATE": TIER_WRITE,
    # ACTUATE-class ops are domain-specific and reserved:
    # "CONTROL_VEHICLE": TIER_ACTUATE, "SET_OUTPUT": TIER_ACTUATE, ...
}


def risk_tier(op_type: str) -> str:
    return _OP_TIERS.get(str(op_type).upper(), TIER_SAFE)


# ── tokens ────────────────────────────────────────────────────────────
_SIG_LEN = hashlib.sha256().digest_size  # 32

def issue_token(secret: str, role: str = "supervisor",
                ttl: int = 300) -> str:
    """Mint a short-lived, role-scoped supervisor token (HMAC-SHA256).

    Framing: the signature is the FIXED last 32 bytes of the payload.
    (A dot-delimited format would break whenever the raw signature
    contains a 0x2E byte -- ~12% of tokens -- silently invalidating
    them; see verify_token.)"""
    if not secret:
        raise ValueError("governance: no supervisor secret configured")
    exp = int(time.time()) + ttl
    payload = f"ss:{role}:{exp}".encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    raw = payload + sig
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_token(token: str, secret: str) -> str | None:
    """Return the role if the token is authentic and unexpired, else None."""
    if not token or not secret:
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        if len(raw) <= _SIG_LEN:
            return None
        sig, payload = raw[-_SIG_LEN:], raw[:-_SIG_LEN]
        expect = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        parts = payload.decode().split(":")
        if len(parts) != 3 or parts[0] != "ss":
            return None
        role, exp = parts[1], int(parts[2])
        if time.time() > exp:
            return None
        return role
    except Exception:
        return None


# ── the gate ──────────────────────────────────────────────────────────
def check_multiparty(tokens: dict[str, str], secrets: dict[str, str],
                    required: set[str], ttl: int = 300
                    ) -> tuple[bool, list[str], list[dict]]:
    """Multi-party approval (federation multi-homing spec, 6.1): an op
    that crosses trust boundaries needs a valid token from EVERY
    required authority.  Returns (ok, missing_or_invalid, verdicts).

    tokens:  {authority: token}  -- the op's auth_tokens map
    secrets: {authority: secret} -- each authority's own HMAC secret
    required: set of authorities that must ALL approve
    """
    verdicts: list[dict] = []
    missing: list[str] = []
    ok = True
    for role in sorted(required):
        tok = tokens.get(role, "")
        if not tok:
            missing.append(role)
            ok = False
            verdicts.append({"role": role, "verdict": "MISSING"})
            continue
        verified = verify_token(tok, secrets.get(role, ""))
        if verified is None:
            missing.append(role)
            ok = False
            verdicts.append({"role": role, "verdict": "DENY",
                             "detail": "invalid or expired token"})
        else:
            verdicts.append({"role": role, "verdict": "APPROVE"})
    return ok, missing, verdicts


class Governance:
    """Per-deployment authorization policy.

    mode:
      off         -- legacy: governed ops execute without auth (default)
      enforce     -- governed ops missing auth+reason DENY the whole plan
      interactive -- missing auth prompts the human; y/n is logged
    """

    def __init__(self, mode: str = "off", governed_ops: set[str] | None = None,
                 secret: str | None = None, log_path: str | Path | None = None,
                 stdin=None):
        if mode not in ("off", "enforce", "interactive"):
            raise ValueError(f"governance mode must be off|enforce|interactive, "
                             f"got {mode!r}")
        self.mode = mode
        self.governed_ops = {str(t).upper() for t in (governed_ops or ())}
        self.secret = secret
        self.log_path = Path(log_path) if log_path else None
        self._stdin = stdin  # injectable for tests; None -> input()

    # -- audit ---------------------------------------------------------
    def record(self, entry: dict):
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def record_all(self, entries: list[dict]):
        for e in entries:
            self.record(e)

    # -- multi-party gate (federation spec 6.1) -------------------------
    def gate_multi(self, tokens: dict[str, str],
                   secrets: dict[str, str], required: set[str],
                   plan_sig: str | None = None,
                   summary: str = "multi-party governed op") -> dict:
        """Require EVERY authority in `required` to present a valid
        token.  Always audited; never mutates the plan."""
        ok, missing, verdicts = check_multiparty(tokens, secrets, required)
        entry = {
            "ts": time.time(), "op": "MULTI_PARTY", "summary": summary,
            "plan_sig": plan_sig, "mode": self.mode,
            "required": sorted(required), "missing": missing,
            "verdict": "APPROVE" if ok else "DENY",
            "detail": "; ".join(f"{v['role']}={v['verdict']}"
                                for v in verdicts),
            "tokens_present": sorted(tokens),
        }
        self.record(entry)
        return entry

    # -- per-op check ---------------------------------------------------
    def _check_op(self, op: dict, plan_sig: str | None) -> dict:
        otype = str(op.get("type", "")).upper()
        summary = _op_summary(op)
        entry = {
            "ts": time.time(), "op": otype, "summary": summary,
            "plan_sig": plan_sig, "mode": self.mode,
            "auth_present": bool(op.get("auth_token")),
            "reason_present": bool(op.get("reason")),
            "reason": op.get("reason") or "",
        }
        if not op.get("reason"):
            entry.update(verdict="DENY", detail="missing reason")
            return entry
        tok = op.get("auth_token")
        if not tok:
            entry.update(verdict="DENY",
                         detail="missing auth_token (supervisor approval required)")
            return entry
        role = verify_token(tok, self.secret or "")
        if role is None:
            entry.update(verdict="DENY", detail="invalid or expired auth_token")
            return entry
        entry.update(verdict="APPROVE", role=role, detail=f"token ok role={role}")
        return entry

    # -- whole-plan gate -------------------------------------------------
    def gate(self, ops: list[dict], plan_sig: str | None = None) -> list[dict]:
        """Check every governed op.  Returns audit entries ([] in off mode).
        mode off -> governed ops pass silently (legacy behaviour)."""
        if self.mode == "off":
            return []
        entries = []
        for op in ops:
            otype = str(op.get("type", "")).upper()
            if otype in self.governed_ops:
                entries.append(self._check_op(op, plan_sig))
        return entries

    # -- human override --------------------------------------------------
    def human_approve(self, entry: dict) -> bool:
        """Prompt the operator for a governed op.  Always logged."""
        prompt = (f"[governance] {entry['op']} {entry['summary']} "
                  f"-- reason: {entry.get('reason') or '(none)'} -- "
                  f"approve? [y/N] ")
        try:
            if self._stdin is not None:
                print(prompt, end="", file=sys.stderr, flush=True)
                line = self._stdin.readline()
            else:
                line = input(prompt)
        except (EOFError, OSError):
            line = ""
        yes = line.strip().lower() in ("y", "yes")
        entry["verdict"] = "HUMAN_APPROVE" if yes else "HUMAN_DENY"
        entry["authorizer"] = "human"
        return yes

    # -- messaging --------------------------------------------------------
    def describe(self, entries: list[dict]) -> str:
        parts = []
        for e in entries:
            v = e.get("verdict", "?")
            parts.append(f"{v}: {e['op']} {e['summary']} -- {e.get('detail')}")
        return "; ".join(parts)


def _op_summary(op: dict) -> str:
    otype = str(op.get("type", "")).upper()
    if otype in ("EXECUTE",):
        return str(op.get("command") or "")
    if otype in ("WRITE", "APPEND", "CONFIG_UPDATE"):
        return f"{op.get('path') or '?'} ({len(str(op.get('content') or ''))} bytes)"
    if op.get("path"):
        return str(op.get("path"))
    if op.get("pattern"):
        return f"pattern={op.get('pattern')}"
    return "?"
