# Phase 4 — Security gate status (2026-09-04)

## Battery (required)

```bash
python3 scripts/redteam_battery.py   # self-bootstraps /tmp/ss_redteam/
```

**Result (this session):** `PASS=41` · `FAIL=0` · `INFO=4` (exit 0).  
**Reconfirmed 2026-09-04 (later):** same `PASS=41` · `FAIL=0` · `INFO=4`.

INFO items = accepted risks (also in `docs/redteam-findings.md`):

| ID | Finding | Treatment |
|----|---------|-----------|
| W1 | Governance token replay within TTL / across reason strings | Accepted-by-design — TTL-window credentials for multi-op plans; future nonce/session binding |
| W3 | Registry feedback has no freshness window | Accepted-by-design — feedback only from trusted mesh peers; future freshness window |

## Strix (scoped surfaces)

**Attempted 2026-09-04** (RAM ~6 GiB avail; Docker Strix sandboxes cleaned first).

| Run | Model path | Outcome |
|-----|------------|---------|
| `swarmstate_9360` | glm-5.3-flash via failover proxy | `MaxTurnsExceeded` — empty tool calls; 0 findings |
| `swarmstate_6055` | mistral-small-latest via proxy | same; 0 findings; ~$0.019 |
| `swarmstate_bdd4` | mistral + `max_tokens` clamp | same pattern (stopped) |

Blockers (not SwarmState code defects):

1. **Tool-use** — free/small models return `NextStepFinalOutput` without `finish_scan` / lifecycle tools.
2. **Credits** — OpenRouter 402 when Strix asks for 16k–65k `max_tokens` (proxy now clamps via `STRIX_FAILOVER_MAX_TOKENS=4096`); direct DeepSeek balance empty.
3. **Install quirk** — bare `strix` hits `KeyError: agents.models` unless agents is pre-imported (wrapper `/tmp/ss-strix-run.py`).

Models added to `~/.strix/failover_proxy.py` + `menu_model.py`: `deepseek/deepseek-chat`, `deepseek/deepseek-v4-flash`.

**Re-run when** a frontier tool-capable model has budget (OpenRouter Claude with credits, or equivalent), using the pre-import wrapper + clamped proxy:

```bash
cat > /tmp/ss-strix-scope.txt <<'EOF'
Focus only on: (1) meshd TCP bind/framing/relay trust,
(2) swarmstate-node HTTP UI/API (/api/state, /api/config, /api/gov/*, /qr.js),
(3) governance token/reason gate and identity store.
Do not expand to unrelated files. Prefer authz/IDOR/injection on those surfaces.
EOF

nice -n 19 ionice -c3 /home/grave/.strix/venv/bin/python /tmp/ss-strix-run.py
# findings → ./strix_runs/; triage → fix → re-run battery
```

Or: `swarmcli collaborate --scan-mode quick` once Strix env works headlessly.

## Gate closure checklist

- [x] Redteam battery green (0 FAIL) — reconfirmed 2026-09-04: PASS=41 INFO=4
- [x] Accepted-risk list recorded (W1, W3)
- [~] Strix quick pass attempted; **blocked on LLM tool-use/credits** (not on host RAM)
- [ ] Strix findings triaged (none produced)
- [ ] (MSP-only) firm-review decision at pilot-proposal time

## Adv-review control-plane hardening (2026-09-04)

See `docs/adv-fix-graph.md`. Landed: localhost-only `/api/gov/issue`, authenticated `join_ok`/`member_added`, default-deny empty allow (open via `--demo`/`--open-mesh`), `gov_ui` no default secret + `127.0.0.1`, LoRa `require_psk` / `SS_LORA_REQUIRE_PSK`, find `-fprintf` denylist. Strix gate still open.
