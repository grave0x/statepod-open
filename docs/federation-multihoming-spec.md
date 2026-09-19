# Federation Membership and Multi-Homing — Spec Addition

**Status:** Draft for spec integration
**Applies to:** Common Domain Adaptation Spec v1.0, Section 5 (Pool and Federation Model)

## 1. Overview
Organisations rarely belong to a single trust domain. Multi-homing = a node/organisation holds
memberships in multiple pools and maintains scoped bridges between them, without contaminating
registries or confusing roles.

## 2. Core Principles
| Principle | Meaning |
|-----------|---------|
| Identity is pool-scoped | A node may hold different identities, roles, permissions per pool. |
| Registry partitions are per-pool | Knowledge in one pool does not leak into another unless explicitly bridged. |
| Bridges are independent | Multiple bridges run simultaneously with independent policy/expiry/scope. |
| Governance crosses boundaries | Cross-pool high-risk actions may require multi-party approval. |
| UI context is explicit | Users always know which federation they act within. |

## 3. Node Membership
One node joins many pools; each membership = separate signed invite, pool-scoped identity/role,
separate registry partition. Example: farm node in farm pool (owner), Landmark client pool (client),
CFS emergency bridge (reporter).

## 4. Multi-Pool Registry Model
- Partitioning: `node.registry → pool:<pool_id> → STRAT:* / MODEL:*`.
- Learning isolation: samples never cross partitions unless a bridge allows it.
- Cross-pool learning via bridges: only what the bridge allows (strategy rates, query routing,
  aggregated stats). Raw task content never shared unless explicitly allowed.

## 5. Multi-Bridge Management
An organisation may keep several bridges active at once (CFS alerts, SES road closures, insurance
evidence, equipment supplier), each with its own allowed message types, task-signature globs,
data-minimisation rules, expiry, and governance requirements.

## 6. Governance Across Pools
Multi-party approval: a cross-boundary action carries auth tokens from each authority
(`"auth_tokens": {"landmark": "sig...", "cfs": "sig..."}`). The kernel verifies all signatures and
checks each role's scope. Cross-pool actions are logged in the bridge ledger (APPROVE/DENY with
full context).

## 7. UI Context Switching
The UI always shows the active federation (`Viewing: Landmark internal [switch]`), with
role-appropriate widgets, pool-specific tasks/alerts, and active bridge status. Switching is
manual or automatic (membership, task, emergency mode).

## 8. Acceptance Criteria
Membership (join multiple pools, separate identities); Isolation (no cross-partition samples unless
bridged); Bridge independence (multiple bridges without interference); Multi-party governance;
Audit trail (full context); UI clarity (visible/swappable federation context).

## 9. Implementation Notes
- Mesh daemon already supports multiple peer connections; multi-pool membership = separate pool
  links per daemon, each with its own partition.
- Registry namespace: `STRAT:<sig>:<strat>:<model>` → `POOL:<pool_id>:STRAT:<sig>:<strat>:<model>`.
- Bridge ledger: already built (scoped bridges); multi-bridge = multiple ledgers, independent policies.
- Governance tokens: extend to multiple signatures + role claims.
- UI shell: federation context indicator + switcher as a core widget.

## 10. Conclusion
Multi-homing turns StatePod into a federation-of-federations platform: many private networks,
separate knowledge, explicit coordinated governance. The cage stays closed, the pools stay clean,
the bridges stay explicit — that's how trust scales.

---

## Appendix E. Implementation Status (agent note — NOT part of the draft)

| Acceptance criterion | Status | Where |
|---|---|---|
| Membership: node joins multiple pools, separate identities | **SHIPPED + demo** | one MeshDaemon per pool link; `scripts/multi_homing_demo.sh` (farm/landmark/cfs) |
| Isolation: per-pool registry partitions | **SHIPPED** | bridged learning is SOURCE-TAGGED (`reg/POOL/<src>/STRAT/...` -> `POOL:<src>:STRAT:...`); local routers read only untagged keys, so foreign learning cannot pollute local partitions; `registry_bridge` records namespaced keys |
| Bridge independence: multiple bridges, no interference | **SHIPPED + demo** | `_chain_on_op` lets several `PoolBridge`s share one node (was: second bridge clobbered the first's relay — real bug found); independent policies + ledgers proven 2 bridges / 3 pools |
| Multi-party governance | **SHIPPED** | `governance.check_multiparty` + `Governance.gate_multi` (every required authority must present a valid token; missing/invalid -> DENY with the list; always audited) |
| Audit trail | **SHIPPED** | bridge ledgers + governance JSONL audit (multi-party entries carry required/missing/verdict) |
| UI clarity: federation context | **SHIPPED** | node web UI: federation context switcher (all pools / per-pool) + a Federation widget rendering each foreign pool's STRAT/MODEL partition (keys shown without the `POOL:<pid>:` prefix); local `tasks` widget shows ONLY the local partition |

Canonical mesh target forms (standardized during this work):
`reg/STRAT/<sig>/<strat>` and `reg/MODEL/<qsig>/<model>` (slash separators; the
colon forms used by early demos were internally consistent but never reach the
kernel registry — the flip demo and `registry_bridge` always used slashes).

---

## Appendix F. C-node Pool Join (C-side) + Invite Hardening (agent note — NOT part of the draft)

### F.1 C-node client-side join — SHIPPED

The Windows/standalone C node (`win/statepod-node.c`) joins pools as a
**client** of a Python pool hub:

- `--join INVITE` — presents the signed invite on every outbound
  connection to the dialed hub (`{"type":"join","invite":...,"name":...}`).
- `join_ok` — adopts the pool name as the UI context label and rebuilds
  the member allowlist from `members` (ops from every pool member are
  then accepted).
- `join_denied` — surfaces a clear alert (bad/expired invite).
- `member_added` — keeps the allowlist current as members join.
- `/api/state` exposes `"join":<invite>` so the SPA's QR widget renders
  the invite as a real QR (embedded MIT qrcode-generator served at
  `/qr.js`).

Verified live: Python hub (`--pool-secret`) + C node with `--join` →
context `farm`, 3 members, learning ops flow both ways (hub tail 0→2,
converged state hash). Invite verification stays **hub-side** (HMAC-SHA256
in `harness/pool.py`); the C node carries the credential, it never verifies
it.

### F.2 Invite hardening — SHIPPED (red team)

- **Single-use per identity** (`PoolHub`): an invite's nonce is tracked in
  a bounded map (`MAX_USED=8192`, expired nonces pruned). Replay by a
  DIFFERENT node id is denied (stolen/shared invite); replay by the SAME
  id is a reconnect and stays accepted.
- Regression test: `test_pool_invite_single_use_per_identity`; bridge and
  multi-homing tests now use per-device invites (the real flow).

### F.3 Remaining C-side gaps (honest)

- The C node cannot HOST a pool (hub-side invite verification is Python).
- Windows EXECUTE still denied (fork→ENOSYS); AST ops graceful-fail
  (tree-sitter stubs); GREP is a regex subset.
