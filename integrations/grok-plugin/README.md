# SwarmState → Grok Build plugin (v0.2)

Deepest containment wiring Grok’s hook API allows. Shares
`~/.swarmstate/omp.json` with the prime-agent extension.

| Piece | Role |
|-------|------|
| `/swarmstate` | `on` / `off` / `status` / `stats` / `full` / `cap` |
| `PreToolUse` | Cap `read_file` / lean-ctx full→signatures / rewrite bare `cat` / grep `-m` |
| `PostToolUse` | Archive oversized `toolResult` + ledger (side effects only) |
| `SessionStart` | Scrollback reminder (stderr) |
| `scripts/statusline.sh` | Optional kernel status row |
| `scripts/contain.py` | Manual DELTA archive of a dump |
| `PARITY.md` | Honest prime vs Grok gap table |

## Install / update

```bash
grok plugin install /home/grave/Projects/internal.source/02-tools/swarmstate/integrations/grok-plugin --trust
# or: grok plugin update swarmstate
```

Optional status line in `~/.grok/config.toml`:

```toml
[ui.status_line]
type = "command"
command = "~/Projects/internal.source/02-tools/swarmstate/integrations/grok-plugin/scripts/statusline.sh"
refresh_interval = 30
```

## Smoke

```bash
python3 integrations/grok-plugin/scripts/test_containment.py
python3 integrations/grok-plugin/scripts/swarmstate_cli.py on
python3 integrations/grok-plugin/scripts/swarmstate_cli.py status
```

## Hard limit

Grok **cannot** rewrite tool results after they run (PostToolUse stdout is
ignored). This plugin hardens inputs + archives oversized outputs. For
in-context DELTA digests, keep lean-ctx on and/or use prime’s extension.
See `PARITY.md`.
