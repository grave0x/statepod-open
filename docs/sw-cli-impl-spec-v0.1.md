# sw CLI Implementation Spec v0.1

**Status:** IMPLEMENTED (v1 + v2 shipped; 2026-08-29)
**Companion to:** `docs/sw-cli-ux-spec-v0.1.md` (the UX surface this implements)
**Entry point:** `bin/swarmcli` (canonical), installed as `~/.local/bin/swarmcli`; `sw` is a one-release compat shim

---

## 1. Architecture

`sw` is a thin CLI over the existing Python harness. It does NOT duplicate
the orchestrator: it reuses `Orchestrator.ask` (plan → gate → execute →
retry ladder), the shared C kernel (`py.swarmstate.SwarmState`), the mesh
daemon (`harness/meshd.py`), and the governance layer
(`harness/governance.py`).

```
bin/sw ──> harness/orchestrator.py  (ask / plan_callback seam)
        ──> harness/governance.py   (gate, tokens, audit)
        ──> harness/meshd.py        (--mesh inference broker)
        ──> py/swarmstate.py        (C kernel: READ/WRITE/EXECUTE/registry)
        ──> ~/.swarmstate/          (session, rates, audit, work root)
```

## 2. Persistent state (`~/.swarmstate`)

| File | Purpose |
|---|---|
| `session.json` | last query/plan/result, checkpoint sha, pool context, mesh daemon info |
| `rates.json` | sw's cross-process registry memory: `{sig: {ok, total}}` from `sw feedback` |
| `audit.jsonl` | governance audit ledger (written when `--governance` is on) |
| `work/` | git-seeded repo the kernel operates on (`build_repo` corpus); `.swarmstate/` is gitignored |
| `work/.swarmstate/` | kernel state (events, checkpoints) — never committed |

**Why a rates sidecar?** The C kernel's registry is **in-memory per process**
(`ss_state_new` never loads perf from disk; `ss_state_load` rewrites repo
files — it is a checkpoint restore, not a registry load). In the mesh design
the mesh is the memory; for a standalone CLI the sidecar is the memory.

## 3. Command → internals mapping

| Command | Path | Notes |
|---|---|---|
| `sw plan "q"` | `_plan_only` (make_plan + validate + blind-write guard) | never executes |
| `sw ask "q"` | `Orchestrator.ask(plan_callback=confirm)` | ONE planning pass (see §5) |
| `sw run` | stored plan → governance gate → `_exec` | re-executes the approved plan |
| `sw diff` | `git diff HEAD` in the work root | |
| `sw rollback` | `git checkout <checkpoint> -- .` | checkpoint = pre-ask HEAD |
| `sw explain <path>` | kernel `read` + `ast_parse` | |
| `sw stats` | kernel status + rates sidecar | |
| `sw feedback ok/weak` | `state.feedback(sig, ok)` + sidecar | per-op signatures of the last plan |
| `sw mesh` | status (no daemon managed by sw) | |
| `sw pool` | session pool context | set with `--pool <name>` |
| `sw gov audit/issue/verify` | audit ledger + `issue_token`/`verify_token` | see §4 |
| `sw research <query\|urls>` | `research.gather` + `research.write_kit` | keyless search (bing/ddg/wikipedia/arxiv), fetch + decode (html/pdf/office/rtf/json/xml/csv/txt) → deep-analysis-labelled kit under `./research/<topic>/`; narrative via `deep_synthesize` when a brain is configured (see README, `harness/research.py`) |
| `sw demo` | `scripts/pitch_demo.sh` | guided pitch mode |
| `sw eval sync\|list\|run [pack\|names]` | `cmd_eval` → `frontierharness.sync/select/run` | FrontierHarness Eval through sw's one-shot path (`harness/main.py`) in isolated seeded scratch repos; outcomes `DENY`/`FAIL`/`TIMEOUT`/`ERROR` — `PASS` unreachable (FHE verifiers private); report → `eval/<ts>/report.md` + `results.jsonl`; records `last_eval`; `--backend mock` runs offline |

## 4. Governance

- Flags (global AND mirrored on `ask`/`run` subparsers):
  `--governance off|enforce|interactive`, `--governed-op TYPE`,
  `--supervisor-secret SECRET`, `--mock-auth-token`.
- `interactive` reads stdin for BOTH the plan confirmation and the human
  approval (one terminal session: `sw ask ... --governance interactive`).
- `--mock-auth-token` mints a REAL token with `issue_token(secret)` so the
  deterministic mock-backend demo path genuinely APPROVEs.
- Verified live matrix (all audited):
  enforce + valid token → **APPROVE** + execute;
  enforce, no token → **DENY** (missing reason);
  enforce, bad token → **DENY** (invalid/expired);
  interactive + "y" → **HUMAN_APPROVE** + execute.

## 5. Single-plan ask (plan_callback seam)

`Orchestrator.ask(query, plan_callback=cb)` invokes `cb(final_plan)` after
the blind-write guard and before governance stamping/execution. The CLI's
callback prints the plan and waits for confirmation; raising aborts the
whole ask with NOTHING executed and NO plan stored. This replaced the v1
double planning pass (plan-only preview + full ask = two model calls).

## 6. Mesh inference broker (`--mesh`)

- Spawns a local `MeshDaemon` (default port 7800) with
  `registry_bridge` on_op, joins `--mesh-peer HOST:PORT` peers, sets
  backend=mesh so `make_plan` routes through the capability broker.
- **Provider probe:** after `ready.wait(3)`, if `daemon.capabilities` is
  empty, falls back to the local model immediately (skips the 20 s broker
  timeout). C nodes announce capabilities (ollama detect) and are detected;
  plain Python meshd peers never announce and cannot serve as providers.
- mesh:// consumers degrade to their own model on timeout (same semantics
  as the orchestrator).

## 7. Testing

- `SW_BACKEND=mock` forces the deterministic mock planner (offline tests,
  no ollama). `SWARMSTATE_LIB` points at `libswarmstate.so`.
- Regression tests in `harness/test_loop.py`:
  - `test_sw_cli_plan_mock_backend`
  - `test_sw_cli_gov_issue_verify_audit`
  - `test_sw_cli_ask_governance_matrix` (interactive approve + enforce DENY + ledger)
  - `test_sw_cli_ask_single_planning_pass`
  - `test_ask_plan_callback_gates_before_execution` (harness seam)

## 8. Status vs the UX spec

| UX spec item | Status |
|---|---|
| Core commands (ask/plan/run/diff/explain/stats/mesh/pool/feedback/rollback) | SHIPPED |
| `sw demo` (guided pitch mode) | SHIPPED (wraps `scripts/pitch_demo.sh`, end-to-end validated) |
| Default offline-local, local model preferred | SHIPPED |
| Output status lines ok/WEAK/DENY/FAIL | SHIPPED (parsed from orchestrator render) |
| Flags --local/--cloud/--mesh/--dry-run/--debug/--model/--pool/--yes | SHIPPED (+ --governance family, --mesh-*) |
| Role awareness (user/support/architect) | **DORMANT (defined, not enforced)** — identity store shipped (`sw identity issue/import/show/clear`); roles + CAPS table in `harness/identity.py`; base CLI runs unrestricted (coding harness); enforcement to be switched on later or wrapped (MSP/enterprise). Expiry reported, would-degrade to user; no identity = unrestricted |
| `sw collaborate` (Strix pentest + findings fold-back) | SHIPPED — headless `strix -n` scan of a local repo/URL; summary -> `~/.swarmstate/strix/<run>.md`, `session.json.last_strix`, remediation hint feeds `sw ask`; `--findings [RUN]` (no new scan), `--detach`, `--console` (`strix view --no-open`); module `harness/strix_collab.py` |
| 5-minute demo without env export / manual server start | SHIPPED (PATH install; `sw demo`) |
