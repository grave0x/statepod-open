---
name: swarmstate
description: >
  SwarmState delegation for jcode sessions. Use when the user wants repo-scale
  analysis, multi-harness comparison, or wants to toggle containment. Shares
  ~/.swarmstate/omp.json with prime-agent and Grok.
---

# SwarmState (jcode) v0.1

Lean-ctx-style reads and `sw` delegation from inside jcode sessions.

## Lean-ctx read patterns (preferred over full-file dumps)

```bash
# Always check size first
wc -l <file>

# Specific line range (preferred — always bounded)
sed -n 'A,Bp' <file>

# Head / tail (bounded)
head -n 50 <file>    # first 50 lines (signatures/overview)
tail -n 30 <file>    # last 30 lines (recent changes/ERBOSE logs)

# Search with result cap
grep -n -m 20 "pattern" <file>

# Count before reading
wc -l <file> && echo "--- reading first 200 lines ---"
head -n 200 <file>
```

## SwarmState delegation

jcode is DeepSeek-backed and great at single-file edits and explanations.
For repo-scale questions that need a wider view:

```bash
# Ask all harnesses in parallel, summary table
sw ask "find all authentication patterns in this repo"

# Explain a pattern across the codebase
sw explain "JWT validation pattern"

# Run a specific task via a named harness
sw run grok "add tests for auth.py"
sw run omp "review this diff"
```

## Containment

`~/.swarmstate/omp.json` is shared with prime-agent and Grok. Lean-ctx-style
reads are the containment path for jcode (no PreToolUse hook available).
