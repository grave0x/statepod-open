# sw CLI UX Spec v0.1

**Status:** Draft for implementation  
**Purpose:** Define the public command-line interface for SwarmState as a daily-use coding harness.

## 1. Command Name

`sw`

- Short, memorable, and free of historical or platform conflicts.
- Expands to **Swarm** or **Software** depending on context.
- Full project name remains **SwarmState**.

## 2. Core Commands

```bash
sw ask "query"          # Plan and optionally execute a task
sw plan "query"         # Generate a plan only
sw run                  # Execute the last approved plan
sw diff                 # Show changes since last execution
sw explain <path>       # Summarise code or document
sw stats                # Show registry and routing stats
sw mesh                 # Show mesh status and peers
sw pool                 # Show current pool and federation context
sw feedback ok|weak     # Mark the last task outcome
sw rollback             # Revert to previous checkpoint
```

## 3. Default Behaviour

- `sw ask` plans, displays, and asks for confirmation before any write/execute.
- `sw plan` never executes.
- `sw run` re-executes the last confirmed plan.
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

`sw` respects the current role and pool context:

- `field` — basic tasks only
- `support` — diagnostics and remediation ops
- `architect` — full system management

Role is derived from the active signed identity or session.

## 7. Demo Readiness

All commands must be usable in a 5-minute live demo without requiring:

- editor configuration
- environment variable export
- manual server startup

A single `sw demo` command may launch the guided pitch mode after red-team pass.

---

## Appendix A. Implementation status (agent note — NOT part of the draft)

Shipped 2026-08-29 as **sw CLI v1 + v2** (`bin/sw`, installed at
`~/.local/bin/sw`). Details in `docs/sw-cli-impl-spec-v0.1.md`.

| Spec item | Status |
|---|---|
| Core commands §2 (ask/plan/run/diff/explain/stats/mesh/pool/feedback/rollback) | **SHIPPED** |
| `sw demo` (§7) | **SHIPPED** — launches `scripts/pitch_demo.sh` (validated end-to-end) |
| Default behaviour §3 (offline-local, confirm before write, plan never executes) | **SHIPPED** — ask shows the FINAL plan once (`plan_callback` seam) and confirms unless `--yes`/`--dry-run` |
| Output style §4 (plain English + status line) | **SHIPPED** — `ok`/`WEAK`/`DENY`/`FAIL`; no stack traces unless `--debug` |
| Flags §5 | **SHIPPED** — all eight + governance family (`--governance`, `--governed-op`, `--supervisor-secret`, `--mock-auth-token`) and `--mesh-port/--mesh-peer/--mesh-allow` |
| Role awareness §6 (user/support/architect) | **DORMANT (defined, not enforced)** — `sw identity issue/import/show/clear` ships (HMAC-SHA256 + TTL, chmod 600 file, secret never stored); roles + capability table live in `harness/identity.py` and are deliberately NOT gating the CLI while sw is a coding harness. Enforcement switches on later, or via an MSP/enterprise wrapper, without changing the CLI surface. Expired identities are reported (would degrade to user); no identity = unrestricted (demo/back-compat, documented) |
