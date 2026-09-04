---
name: swarmstate
description: >
  SwarmState containment for Grok Build. Use when the user runs /swarmstate,
  asks to enable/disable containment, or wants kernel status / savings stats.
  Shares ~/.swarmstate/omp.json with the prime-agent extension.
---

# SwarmState (Grok) v0.2

Deep wiring of SwarmState containment into Grok Build hooks. Toggle persists
to `~/.swarmstate/omp.json` (same file prime uses).

## Commands

```bash
python3 "$SWARMSTATE_REPO/integrations/grok-plugin/scripts/swarmstate_cli.py" \
  on|off|status|stats|full <id>|cap <chars>
```

Default `SWARMSTATE_REPO=~/Projects/internal.source/02-tools/swarmstate`.

| Subcommand | Effect |
|------------|--------|
| `on` / `off` | Enable / disable containment |
| `status` | ON/OFF, caps, kernel status line |
| `stats` | Sum containment ledger |
| `full <id>` | Locate archived outbox dump |
| `cap <n>` | Set `capChars` (≥1000) |

## What "on" does in Grok

1. **PreToolUse** — rewrite dump-shaped `read_file` / lean-ctx `mode=full`→`signatures` / bare `cat`→`head` / grep without `-m`.
2. **PostToolUse** — archive `toolResult` over `capChars` + ledger (side effects; stdout ignored by Grok).
3. **SessionStart** — stderr reminder in scrollback.
4. **Guidance** — one `additionalContext` note per session on first PreToolUse.
5. **Status line** (optional) — `scripts/statusline.sh`.

## Prefer when ON

- `sed -n 'A,Bp'`, `grep -n -m`, `head`/`tail`, lean-ctx `ctx_read(mode=signatures|map)`
- `swarmcli ask` / `swarmcli explain` for repo-scale questions
- Never `cat` whole large files

## Hard gap vs prime

Grok cannot swap tool results for DELTA digests. See `PARITY.md`. Keep lean-ctx enabled.
