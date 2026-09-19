---
description: StatePod containment — on | off | status | stats | full <id> | cap <n>
argument-hint: "[on|off|status|stats|full <id>|cap <chars>]"
---

Run the StatePod Grok CLI (shares `~/.statepod/omp.json` with prime):

```bash
REPO="${STATEPOD_REPO:-$HOME/Projects/internal.source/02-tools/statepod}"
python3 "$REPO/integrations/grok-plugin/scripts/statepod_cli.py" $ARGUMENTS
```

Report the CLI stdout to the user verbatim.

If they passed `on`, remind them:
- PreToolUse will cap/rewrite dump-shaped reads and shell `cat`/`grep`.
- PostToolUse archives oversized results under `~/.statepod/grok/outbox/` (ledger for `stats`).
- Grok still cannot rewrite tool *results* in the model context — lean-ctx + input hardening is the containment path (`integrations/grok-plugin/PARITY.md`).
