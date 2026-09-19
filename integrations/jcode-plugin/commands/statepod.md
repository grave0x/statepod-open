---
description: StatePod containment + delegation — on | off | status | stats | sp <statepod args>
argument-hint: "[on|off|status|stats|sp <statepod args>]"
---

Run the StatePod CLI, sharing `~/.statepod/omp.json` with prime-agent and Grok:

```bash
python3 "$HOME/Projects/internal.source/02-tools/statepod/bin/statepod" $ARGUMENTS
```

Report the output to the user verbatim.

If they passed `sp` (with args), delegate to statepod's full CLI — this gives
jcode sessions access to `sp ask`, `sp explain`, `sp run <harness>`, and all
other statepod commands.
