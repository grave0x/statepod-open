# StatePod → jcode plugin (v0.1)

Bridges statepod into jcode sessions: lean-ctx reads, `sp` delegation, and
shared containment state with prime-agent / Grok.

## What this plugin does

| Piece | Role |
|-------|------|
| `skills/statepod/SKILL.md` | Skill: Lean-ctx read patterns, `sp` delegation |
| `commands/statepod.md` | `/statepod on|off|status|stats` via `sp` CLI |
| Shared state | `~/.statepod/omp.json` (same file prime-agent / Grok use) |

## Install

jcode reads plugins from `~/.config/jcode/plugins/`. Symlink:

```bash
mkdir -p ~/.config/jcode/plugins
ln -s ~/Projects/internal.source/02-tools/statepod/integrations/jcode-plugin \
      ~/.config/jcode/plugins/statepod
```

Or copy the whole `jcode-plugin/` dir there.

## Lean-ctx integration

jcode runs DeepSeek by default (`-p deepseek`). The skill teaches it to use
lean-ctx-style targeted reads instead of full-file dumps:

- `sed -n 'A,Bp'` for specific line ranges
- `grep -n -m N` to find patterns with a result cap
- `head -n N / tail -n N` for file start/end
- `wc -l` before reading to know the size first

## StatePod delegation

For repo-scale questions that jcode can't answer well with local tools, the
skill teaches it to call out to `sp`:

```bash
# sp ask: fan-out to all harnesses in parallel
sp ask "how does X work in this repo"

# sp explain: repo-scale grep with state summaries
sp explain "authentication pattern"

# sp run: single-task run via a specific harness
sp run grok "add a test for the auth module"
```

## Containment

This plugin does NOT add PreToolUse/PostToolUse hooks (jcode has no hook API).
Containment is shared via `~/.statepod/omp.json` — when enabled in Grok or
prime-agent, the containment settings apply to all three. jcode uses lean-ctx
and targeted-read guidance as its containment path.
