# sp CLI UX Spec v0.1

**Status:** Draft for implementation  
**Purpose:** Define the public command-line interface for StatePod as a daily-use coding harness.

## 1. Command Name

`sp`

- Short, memorable, and free of historical or platform conflicts.
- Short for **StatePod**; also evokes **Software**.
- Full project name remains **StatePod**.

## 2. Core Commands

```bash
sp ask "query"          # Plan and optionally execute a task
sp plan "query"         # Generate a plan only
sp run                  # Execute the last approved plan
sp diff                 # Show changes since last execution
sp explain <path>       # Summarise code or document
sp stats                # Show registry and routing stats
sp mesh                 # Show mesh status and peers
sp pool                 # Show current pool and federation context
sp feedback ok|weak     # Mark the last task outcome
sp rollback             # Revert to previous checkpoint
```

## 3. Default Behaviour

- `sp ask` plans, displays, and asks for confirmation before any write/execute.
- `sp plan` never executes.
- `sp run` re-executes the last confirmed plan.
- All commands work offline by default.
- Local model preferred; cloud escalation only when explicitly requested or required by task.

## 4. Output Style

- Plain English first, technical detail second.
- Each result ends with a short status line:
  - `ok`
  - `WEAK`
  - `DENY` (governance)
  - `FAIL`
- No raw stack traces unless `--debug` is set.

## 5. Flags

| Flag | Meaning |
|------|---------|
| `--local` | Use local model only |
| `--cloud` | Allow cloud escalation |
| `--mesh` | Use mesh inference broker |
| `--dry-run` | Show plan without executing |
| `--debug` | Include raw logs and errors |
| `--model` | Specify model name |
| `--pool` | Set current pool context |
| `--yes` | Auto-confirm low-risk plans |

## 6. Role Awareness

`sp` respects the current role and pool context:

- `field` — basic tasks only
- `support` — diagnostics and remediation ops
- `architect` — full system management

Role is derived from the active signed identity or session.

## 7. Demo Readiness

All commands must be usable in a 5-minute live demo without requiring:

- editor configuration
- environment variable export
- manual server startup

A single `sp demo` command may launch the guided pitch mode after red-team pass.

---

## Appendix A. Implementation status (agent note — NOT part of the draft)

Shipped 2026-08-29 as **sp CLI v1 + v2** (`bin/sp`, installed at
`~/.local/bin/sp`). Details in `docs/sp-cli-impl-spec-v0.1.md`.

| Spec item | Status |
|---|---|
| Core commands §2 (ask/plan/run/diff/explain/stats/mesh/pool/feedback/rollback) | **SHIPPED** |
| `sp demo` (§7) | **SHIPPED** — launches `scripts/pitch_demo.sh` (validated end-to-end) |
| Default behaviour §3 (offline-local, confirm before write, plan never executes) | **SHIPPED** — ask shows the FINAL plan once (`plan_callback` seam) and confirms unless `--yes`/`--dry-run` |
| Output style §4 (plain English + status line) | **SHIPPED** — `ok`/`WEAK`/`DENY`/`FAIL`; no stack traces unless `--debug` |
| Flags §5 | **SHIPPED** — all eight + governance family (`--governance`, `--governed-op`, `--supervisor-secret`, `--mock-auth-token`) and `--mesh-port/--mesh-peer/--mesh-allow` |
| Role awareness §6 (user/support/architect) | **DORMANT (defined, not enforced)** — `sp identity issue/import/show/clear` ships (HMAC-SHA256 + TTL, chmod 600 file, secret never stored); roles + capability table live in `harness/identity.py` and are deliberately NOT gating the CLI while sp is a coding harness. Enforcement switches on later, or via an MSP/enterprise wrapper, without changing the CLI surface. Expired identities are reported (would degrade to user); no identity = unrestricted (demo/back-compat, documented) |
