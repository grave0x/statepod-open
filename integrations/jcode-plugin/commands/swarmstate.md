---
description: SwarmState containment + delegation — on | off | status | stats | sw <swarmcli args>
argument-hint: "[on|off|status|stats|sw <swarmcli args>]"
---

Run the SwarmState CLI, sharing `~/.swarmstate/omp.json` with prime-agent and Grok:

```bash
python3 "$HOME/Projects/internal.source/02-tools/swarmstate/bin/swarmcli" $ARGUMENTS
```

Report the output to the user verbatim.

If they passed `sw` (with args), delegate to swarmstate's full CLI — this gives
jcode sessions access to `sw ask`, `sw explain`, `sw run <harness>`, and all
other swarmstate commands.
