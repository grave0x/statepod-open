# StatePod Build Plan v1 — LOCKED

**Date:** 2026-09-04 · **Provenance:** wayfinder map
[Feature completion + auto-tuning spine](https://github.com/grave0x/statepod/issues/1)
(all 10 tickets resolved; see the map's Decisions-so-far for the linked detail).
This is the hand-off spec: every open GAPS.md item is scheduled, killed, or
deferred below. Execution happens in normal build sessions against this plan.

## Spine (fixed)

The **self-tuning loop** is the roadmap's organizing priority: sequence work
by what generates tuning data earliest. It is a *weak* spine — the security
gate is fixed regardless, and pilot-credibility work follows the data track.
Loop boundary: router + rate keys + feedback publication + strategy-at-plan-
time. Full glossary: `CONTEXT.md`.

## Phases (in order)

| # | Phase | Output / acceptance | GAPS |
|---|---|---|---|
| 0 | Corpus maturity bench | **Done 2026-09-04:** `scripts/maturity_bench.py` (`make maturity-bench`) — mock A/B heuristic vs rate-based over 42 tasks; report `~/.local/state/statepod/maturity-bench-latest.json`; latest run pass (1.0/1.0, override honored). Live-LLM confirmation deferred (ponytail: mock proves wiring). | 10 |
| 1 | Strategy-at-plan-time wiring | **Done 2026-09-04:** `STRAT_MIN_SAMPLES=3` in `harness/planner.py`; registry strategy tests updated; planning path uses same ≥3 sample gate as model rates. | 10 |
| 2a | mingw spike (half-day timebox) | **FAIL 2026-09-04** — no mingw `libllama`/headers in distro; Linux `llama.h` conflicts with mingw `uintptr_t`. Re-scoped: keep Ollama on Windows one release; Linux `SP_EMBED_INFER` unchanged. MSVC/static llama.cpp is a future effort. | 11 |
| 2b | Windows embedded build (#4 Phase A) | **Deferred** (blocked on 2a FAIL). | 11 |
| 2c | Delete node-side Ollama paths | **Deferred on Windows**; Linux may drop Ollama fallback later once embed-only is the default path. Harness `--backend ollama` stays. | 11 |
| 3 | Watch loop D1 → D2 | **Done 2026-09-04:** `win/statepod-node.c` `watch_loop` — D1 disk/mem/load1 + registry/hash + mesh peers → `alert_add`; D2 restart link / rejoin pool / resend ops (`hist_flush_all`). Service restarts stay escalation-only. | Support/Ops |
| 4 | Strix security pass (early) | **Partial 2026-09-04:** battery green (41 PASS / 0 FAIL, reconfirmed); W1/W3 accepted. Live Strix attempted (RAM OK) but **blocked on LLM tool-use/credits** — 0 findings; see `docs/phase4-security-gate.md`. Retry with frontier tool-capable model. | 14 |
| 5 | UI gov approve/deny (token variant) + MSP demo script | **Done 2026-09-04:** copy-token UI + `GET|POST /api/gov/{issue,verify,decide}` (POST JSON body; HMAC = `harness/governance.py`); `scripts/msp_demo.sh`. Strix still open on phase 4 gate. | UI kit (#8) |
| 6 | LoRa hardening | **Done 2026-09-04:** `harness/lora.py` — AES-256-GCM (PSK/`SP_LORA_PSK`), priority TX (ACK>high>bulk), per-stream route reliability cache. Hardware half of GAPS 15 still open. | 15 |
| 7 | GAPS truth-sweep + perf re-bench | **Done 2026-09-04:** GAPS aligned (Watch [x], gov console POST [x]); `py/bench.py` pure 5-op batch **201 µs** (<1 ms PASS). AST-10k / SNAP re-bench still optional fog. | fog items |

## Gates

- **Security gate (hard):** no pilot with real client data until closed.
  Closure = Strix findings triaged (high/critical fixed or accepted with
  W1/W3-style documented trust-model notes) + redteam battery green +
  accepted-risk list in this spec + (MSP-only) firm-review decision at
  pilot-proposal time.
- **Maturity gate:** GBNF plan grammar is reconsidered only after the
  phase-0 bench shows stable per-signature rates. Grammar may also return
  via the LoRa bytes-saving thread.

## Killed (do not build)

- Plan templates (oracle-in-loop retry covers it with evidence; revisit only
  if the bench shows high per-signature plan reuse)
- Per-task GPU-offload learning (all-or-nothing NGL policy is the learned
  answer — MX550 bench: partial offload 2× slower than CPU; 74f0eb4)
- Node-side Ollama code paths (after phase 2c)
- TUI / AGS widget UI surfaces (the C-node web UI is the slice)
- Per-symbol span hashes (whole-file invalidation is correct for the
  in-memory cache; revisit only with a persisted symbols.bin index)
- `sp_export_lora` / any C-side LoRA trainer (fine-tuning, if ever, exports
  GGUF-LoRA from the harness)

## Deferred (waiting on a named trigger)

- GBNF plan grammar → maturity gate
- Generated doc summaries → persisted symbols.bin index + opt-in privacy flag
- Phase-4 LoRA adapter selection policy → first real GGUF-LoRA adapter
  existing (mesh exchange already exists via file_request/file_response)
- Runtime inference-layer re-switching → a real user complains
- Governance token nonce/session binding → first multi-tenant deployment

## Conditional / out of plan

- **Paid security firm:** conditional on pilot vertical (CFS likely no, MSP
  likely yes) — decided when the vertical pick is unparked.
- **Real LoRa hardware** (GAPS 15 lab half): its own future effort; nothing
  in this plan blocks it.
- **Pilot vertical pick** (CFS vs MSP): business decision, parked by grave —
  out of this map's scope by design.
