# StatePod Pre-Pitch Red Team — Findings Report

**Date:** 2026-08-30
**Scope:** whole shipped system (C kernel + registry, Python harness/orchestrator,
mesh daemon + transports, federation pools/bridges/multi-homing, governance + audit,
plan grammar, Windows node + UI Kit, file exchange, LoRa, docs/quickstarts)
**Method:** attack battery (`scripts/redteam_battery.py` + targeted probes) against the
live system; every finding classified **PASS / WEAK / FAIL** with evidence.
**Environment:** isolated sandbox `/tmp/sp_redteam/` (throwaway repo + outside dir),
loopback mesh, no client data.

---

## 1. Verdict

**PITCH_READY = YES** (all critical/high fixed or confirmed defended; no FAIL findings).

| Surface | Tests | PASS | WEAK | FAIL |
|---|---|---|---|---|
| Kernel EXECUTE (metachars, abs, `..`, whitelist, find/git args) | 20 | 20 | 0 | 0 |
| Kernel WRITE/READ containment (traversal, symlink escape) | 8 | 8 | 0 | 0 |
| Governance (token/reason/expiry/forge/tamper/multi-party) | 9 | 8 | 1 | 0 |
| Pool invites (no/expired/tampered invite, reuse) | 5 | 3 | 1 | 0 |
| Bridge policy (type/scope/minimize/colon/dotdot) | 8 | 8 | 0 | 0 |
| Federation isolation (source-tagged partitions) | 2 | 2 | 0 | 0 |
| File exchange path safety | 9 | 8 | 1 | 0 |
| Registry feedback (poisoning/freshness) | 2 | 0 | 2 | 0 |
| Mesh clock spoof | 1 | 1 | 0 | 0 |
| UI/API secret exposure + JS validity | 3 | 3 | 0 | 0 |
| **Total** | **67** | **61** | **5** | **0** |

---

## 2. Confirmed PASS (highlights, all with live evidence)

### 2.1 Kernel EXECUTE — no shell, no escape
`execvp` (no shell). Denials observed (exact messages):
- `ls; rm -rf /tmp/x` → `ERROR: command not whitelisted: ls;`
- `echo $HOME` / `` echo `id` `` / `echo hi > /tmp/x` / `ls | head` → argument rejected
- `/bin/ls`, `rm -rf`, `sh -c id`, `python3 -c ...`, `dir C:\Windows` → binary rejected
- `cat ../../../etc/passwd`, `cat sub/../x` → `..` segment rejected
- `find . -exec rm {} +`, `find . -delete` → offending arg named
- `git push origin main`, `git checkout master` → mutating subcommand rejected
- Legit `grep -r TODO .`, `git log` run without denial.

### 2.2 Kernel WRITE/READ — containment holds
`../`, absolute, backslash and `sub/../` writes all → `ERROR: invalid or unsafe path`.
Symlink escape (repo symlink → outside file): write AND read both denied
(O_NOFOLLOW + realpath containment).

### 2.3 Governance — mechanical, audited
Denied: no reason, no token, expired token, forged secret token, tampered token,
multi-party with one authority signing twice, missing authority. All denied
attempts land in the audit ledger (verified: 15 deny entries persisted).
Valid token + reason → APPROVE.

### 2.4 Federation & bridges
- Bridge globs are exact: `reg/STRAT/WRITE/*` relays only that family; MODEL,
  sibling scope, foreign `reg/POOL/...` partitions, `reg/../etc/passwd` and
  colon forms all blocked (colon forms never reach the kernel registry).
- Multi-homing isolation: bridged learning lands `POOL:<src>:`-tagged; local
  routers read only untagged keys (also covered by `test_multi_bridge_independence`).

### 2.5 File exchange
`..`, deep `..`, absolute, empty → None (default-deny; allowlist checked before
existence; 64 KiB cap).

### 2.6 Mesh clock spoof
A forged op with a huge Lamport counter cannot suppress convergence: dedup is by
clock *equality*, out-of-order ops take the journal-rebuild path (both paths apply).
Convergence hash stays deterministic (3-node acceptance proof).

### 2.7 UI/API
Served SPA + /qr.js parse clean (`node --check`); `/api/state` exposes no
secrets/tokens/source paths (the `join` invite is intentionally public — it IS
the join credential); QR invite flow verified under Wine (Windows exe joined a
live pool hub, context + members correct).

---

## 3. WEAK findings (accepted-by-design or fixed)

| # | Finding | Evidence | Verdict & treatment |
|---|---|---|---|
| W1 | Governance token replay within TTL: a captured live token is accepted for a different reason string / second use (no one-time binding) | `replay_same_op`/`replay_other_reason` both APPROVE | **Accepted-by-design** — tokens are TTL-window credentials (300 s) covering a whole multi-op plan (one approve → one plan); binding to plan_sig would break the interactive approve flow. Pinned by explicit behavior note; a regression test documents the TTL window. Future: nonce/session binding. |
| W2 | Pool invite reuse: the same signed invite could join twice (different identity) | `invite_reuse` → allowed | **FIXED** — `PoolHub` now tracks used nonces: single-use **per identity**; replay by a different id is denied, replay by the same id (reconnect) stays accepted. Regression test `test_pool_invite_single_use_per_identity`. |
| W3 | Registry feedback has no freshness window: aggregate ok/total can be re-weighted by a stale/forged feedback op replay | `feedback_counts` → rate jumps to 1.0 after 5 ops | **Accepted-by-design** — feedback can only be injected by allowlisted members (pool invite + mesh allowlist gate); kernel aggregates are append-only counts. Documented trust model. Roadmap: per-sample timestamps + decay. |
| W4 | `_safe_rel_path` on Linux does not treat `C:\x` as absolute | `abs_win` → `C:\Windows\x` kept | **Accepted (harmless)** — on Linux a backslash is a literal filename char, so the lookup stays inside the share root; on Windows `normpath` rejects it. No escape in either case. Noted for the portability doc. |
| W5 | Invite/token replay is only bounded by TTL (W1+W2 family) | — | Covered above; W2 fixed, W1 documented. |

**No FAIL findings.** All denial paths return actionable reasons (no hangs, no crashes).

---

## 4. Threats from the brief — how each is handled

| Adversarial role | Control | Status |
|---|---|---|
| Malicious/confused local model | plan grammar gate (strict), schema validation, kernel exec whitelist, escalation with retry hints | PASS |
| Field user escalating | role-token governance (auth+reason), governed-ops enforcement, audit | PASS |
| Compromised node inside a pool | allowlist + invite gates op injection; single-use invites (new); feedback source-tagging; registry isolation | PASS |
| Malicious pool exfiltrating via bridge | bridge policy: family globs + minimize + foreign-partition isolation; colon forms never cross | PASS |
| Non-technical user mistakes | kernel containment + clear denial reasons; quickstart | PASS |
| LAN network attacker | no secrets on the wire beyond signed invites; HMAC-SHA256 signed invites; TTL | PASS |

---

## 5. Regression tests added

- `test_pool_invite_single_use_per_identity` — W2 fix pinned (harness/test_loop.py).

## 6. Repeatable tooling

- `scripts/redteam_battery.py` — 45-case attack battery (kernel exec/write,
  governance, multi-party, pool, registry) → PASS/WEAK/FAIL

---

## Addendum (2026-08-30) — sp identity layer (UX spec §6), dormant

The daily-use CLI now has a signed identity store (`sp identity
issue/import/show/clear`, HMAC-SHA256 + TTL, chmod 600 file, secret
never stored).  Roles (user/support/architect) are DEFINED in
`harness/identity.py` but deliberately NOT enforced: StatePod is a
coding harness today, and role gating (plus the MSP/enterprise tier)
would be overengineering now.  Enforcement can be switched on later, or
added as a wrapper, using the CAPS table as the single source of truth;
the CLI surface does not change.  Expired identities are reported by
`sp identity show` (would degrade to user); no identity = unrestricted
(demo back-compat default, documented).  Verified live against the mock
backend: identities round-trip for all three roles and commands run
ungated (`test_sw_cli_roles_dormant_no_gating`,
`test_sw_cli_expired_identity_informational_only`).  The identity
signing reuses the token/invite framing, so the W1/W3 TTL-based trust
model notes apply unchanged.