# SwarmState → jcode plugin (v0.1)

Bridges swarmstate into jcode sessions: lean-ctx reads, `sw` delegation, and
shared containment state with prime-agent / Grok.

## What this plugin does

| Piece | Role |
|-------|------|
| `skills/swarmstate/SKILL.md` | Skill: Lean-ctx read patterns, `sw` delegation |
| `commands/swarmstate.md` | `/swarmstate on|off|status|stats` via `sw` CLI |
| Shared state | `~/.swarmstate/omp.json` (same file prime-agent / Grok use) |

## Install

jcode reads plugins from `~/.config/jcode/plugins/`. Symlink:

```bash
mkdir -p ~/.config/jcode/plugins
ln -s ~/Projects/internal.source/02-tools/swarmstate/integrations/jcode-plugin \
      ~/.config/jcode/plugins/swarmstate
```

Or copy the whole `jcode-plugin/` dir there.

## Lean-ctx integration

jcode runs DeepSeek by default (`-p deepseek`). The skill teaches it to use
lean-ctx-style targeted reads instead of full-file dumps:

- `sed -n 'A,Bp'` for specific line ranges
- `grep -n -m N` to find patterns with a result cap
- `head -n N / tail -n N` for file start/end
- `wc -l` before reading to know the size first

## SwarmState delegation

For repo-scale questions that jcode can't answer well with local tools, the
skill teaches it to call out to `sw`:

```bash
# sw ask: fan-out to all harnesses in parallel
sw ask "how does X work in this repo"

# sw explain: repo-scale grep with state summaries
sw explain "authentication pattern"

# sw run: single-task run via a specific harness
sw run grok "add a test for the auth module"
```

## Containment

This plugin does NOT add PreToolUse/PostToolUse hooks (jcode has no hook API).
Containment is shared via `~/.swarmstate/omp.json` — when enabled in Grok or
prime-agent, the containment settings apply to all three. jcode uses lean-ctx
and targeted-read guidance as its containment path.
