"""Identity: signed role credentials for the sw CLI (UX spec, §6).

An identity is a long-lived, role-scoped credential issued by a
supervisor (architect).  It reuses the governance HMAC-SHA256 framing so
there is ONE signing scheme across supervisor tokens, pool invites, and
identities:

    payload = b"ss-id:v1:<role>:<subject>:<expiry-epoch>"
    line    = base64url(payload + sig)
    sig     = HMAC-SHA256(secret, payload)      (fixed last 32 bytes)

Base-version roles (least -> most privilege) -- DEFINED but
DORMANT.  The CLI does not enforce them while SwarmState is used as a
coding harness:

    user      -- read-only plans (SAFE ops only) + basic commands
                 (explain/stats/mesh/pool/feedback/diff)
    support   -- diagnostics + remediation: WRITE/EXEC plans,
                 gov audit|verify, rollback
    architect -- full system management: everything, incl. gov issue
                 and identity issue

Enforcement is intentionally OFF in the base build.  The identity
machinery (issue/import/verify/store) is in place and waiting: when
roles are actually needed -- or as an MSP/enterprise wrapper -- the
capability table above is the single source of truth to switch on.

Trust model: the signature + expiry are verified AT IMPORT against the
supervisor secret.  After import only the signed line and decoded claims
live on disk (chmod 600); the supervisor secret is NOT stored.  If/when
enforcement is switched on, an expired identity would degrade to
``user`` (least privilege); with no identity file the effective role
would be ``architect`` (unauthenticated demo default, documented --
production deployments issue identities).

Identity file location: $SW_IDENTITY, else ~/.swarmstate/identity.json.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

ROLES = ("user", "support", "architect")
DEFAULT_TTL = 30 * 24 * 3600  # 30 days

# capability -> roles that hold it (spec §6).  DORMANT: the base CLI
# does not enforce these yet (see module docstring); they define the
# shape so a later enforcement pass or MSP wrapper can switch on.
CAPS = {
    # plan/ask restricted to SAFE ops only (READ/GREP/SYMBOL_SUMMARY/...)
    "read_plan": ("user", "support", "architect"),
    # WRITE/EXEC (and WRITE-class) ops in plans
    "write_exec": ("support", "architect"),
    # sw gov audit / verify, sw rollback
    "audit": ("support", "architect"),
    "rollback": ("support", "architect"),
    # sw gov issue / sw identity issue
    "mint": ("architect",),
}

_PREFIX = b"ss-id:v1:"


def role_caps(role: str) -> set[str]:
    """Set of capabilities a role holds."""
    return {cap for cap, roles in CAPS.items() if role in roles}


def issue_identity(secret: str, role: str, subject: str = "",
                   ttl: int = DEFAULT_TTL) -> str:
    """Mint a signed identity line (HMAC-SHA256, same framing as
    governance tokens: signature = fixed last 32 bytes)."""
    if not secret:
        raise ValueError("identity: no supervisor secret configured")
    if role not in ROLES:
        raise ValueError(f"identity: role must be one of {ROLES}, got {role!r}")
    if ":" in subject:
        raise ValueError("identity: subject cannot contain ':'")
    exp = int(time.time()) + int(ttl)
    payload = _PREFIX + f"{role}:{subject}:{exp}".encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    raw = payload + sig
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_identity(line: str, secret: str) -> dict | None:
    """Verify signature + expiry.  Returns claims or None.

    claims: {"role", "subject", "exp"} (exp is the epoch deadline)."""
    if not line or not secret:
        return None
    try:
        pad = "=" * (-len(line) % 4)
        raw = base64.urlsafe_b64decode(line + pad)
        if len(raw) <= hashlib.sha256().digest_size:
            return None
        sig, payload = raw[-32:], raw[:-32]
        expect = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        if not payload.startswith(_PREFIX):
            return None
        body = payload[len(_PREFIX):].decode()
        role, subject, exp = body.split(":")
        if role not in ROLES:
            return None
        exp = int(exp)
        if time.time() > exp:
            return None
        return {"role": role, "subject": subject, "exp": exp}
    except Exception:
        return None


def identity_path(override: str | None = None) -> Path:
    env = override or os.environ.get("SW_IDENTITY")
    if env:
        return Path(env)
    return Path.home() / ".swarmstate" / "identity.json"


def save_identity(path: Path, line: str, secret: str) -> dict:
    """Verify then persist a signed identity (chmod 600).  Returns claims."""
    claims = verify_identity(line, secret)
    if claims is None:
        raise ValueError("identity: invalid, tampered, or expired credential "
                         "(wrong supervisor secret?)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "line": line,
        "role": claims["role"],
        "subject": claims["subject"],
        "exp": claims["exp"],
        "imported_at": time.time(),
    }, indent=1))
    os.chmod(path, 0o600)
    return claims


def load_identity(path: Path) -> dict | None:
    """Read the local identity file.

    Returns None when absent; otherwise {"role", "subject", "exp",
    "expired": bool} -- an expired file is still returned so the caller
    can degrade to least privilege with a warning."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    role = data.get("role")
    if role not in ROLES:
        return None
    exp = int(data.get("exp", 0))
    return {"role": role, "subject": data.get("subject", ""),
            "exp": exp, "expired": time.time() > exp}


def describe(ident: dict | None) -> str:
    """Human-readable identity line for status output."""
    if ident is None:
        return "role: architect (no identity file)"
    role = ident["role"]
    subj = ident.get("subject") or "anonymous"
    if ident.get("expired"):
        return f"role: {role} (subject {subj}, EXPIRED -> would degrade to user)"
    return f"role: {role} (subject {subj})"
