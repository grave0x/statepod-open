# Build Plan Phases 0–1 Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox syntax.

**Goal:** Deliver the maturity-gate artifact (phase 0) and align strategy-at-plan-time with the locked build plan’s ≥3-sample rule (phase 1).

**Architecture:** Reuse `harness/corpus.py` + `task_suite.run_one`. Add `scripts/maturity_bench.py` that A/B’s heuristic vs rate-based strategy selection over the 42-task mock corpus, writes a JSON report, and exits non-zero on regression. Bump `STRAT_MIN_SAMPLES` from 2 → 3 to match `pick_model_rates` / build-plan-v1.

**Tech Stack:** Python 3 stdlib, existing C kernel via `statepod`, mock backend (no network).

**Spec:** `docs/build-plan-v1.md` phases 0–1; glossary in `CONTEXT.md` (maturity gate).

## Global Constraints

- Zero new pip deps; C99/tiny-RSS rules unchanged.
- Throttle builds: `-j4`, `nice -n 19` when compiling.
- Prefer fixing `scripts/corpus_builder.py` (broken `governance=gov`) as part of phase 0 plumbing.

---

### Task 1: Fix corpus_builder Orchestrator kwargs

**Files:**
- Modify: `scripts/corpus_builder.py` (Orchestrator construction)

- [ ] Remove invalid `governance=gov` / undefined `mock_auth_token` kwargs (use defaults).
- [ ] Smoke: `python3 scripts/corpus_builder.py --backend mock --n 2` exits 0.

### Task 2: Phase 1 — STRAT_MIN_SAMPLES = 3

**Files:**
- Modify: `harness/planner.py` (`STRAT_MIN_SAMPLES`)
- Modify: `harness/test_loop.py` (registry strategy tests that seed 2 samples)

- [ ] Set `STRAT_MIN_SAMPLES = 3`.
- [ ] Update tests that prove overrides to seed 3 samples; keep “single sample is noise” and add “two samples still cold”.
- [ ] Run: `python3 -m unittest harness.test_loop.TestLoop.test_registry_strategy_overrides_heuristic_to_delta harness.test_loop.TestLoop.test_registry_strategy_requires_min_samples -v` (and related registry tests).

### Task 3: Phase 0 — maturity bench script + test

**Files:**
- Create: `scripts/maturity_bench.py`
- Create: `harness/test_maturity_bench.py` (or extend `test_loop.py` with focused cases)
- Modify: `Makefile` (optional `maturity-bench` target)

**Behavior:**
1. Heuristic pass: run all `corpus.ALL_TASKS` with strategy forced to op-shape heuristic (no registry influence).
2. Seed pass: record STRAT feedback from heuristic run into a temp jsonl; additionally inject synthetic proven overrides (≥3 samples) on at least one READ signature where heuristic≠proven.
3. Rate pass: replay seeds + run corpus with normal `decide_strategy(registry=…)`.
4. Report per-signature strategy picks + semantic rates; write `docs/maturity-bench-latest.json` (or under `~/.local/state/statepod/`).
5. Pass if: rate-based overall semantic rate ≥ heuristic; and every injected override is honored on the rate pass.

- [ ] Implement script + unit/integration test on mock.
- [ ] Run bench; commit artifact path note in build-plan or CONTEXT if useful.

### Task 4: Checkpoint commit

- [ ] Commit phase 0–1 with conventional message.
- [ ] Stop for user review before phases 2+.
