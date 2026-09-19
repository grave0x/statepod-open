---
name: statepod
description: >
  StatePod delegation for jcode sessions. Use when the user wants repo-scale
  analysis, multi-harness comparison, or wants to toggle containment. Shares
  ~/.statepod/omp.json with prime-agent and Grok.
---

# StatePod (jcode) v0.1

Lean-ctx-style reads and `sp` delegation from inside jcode sessions.

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

## StatePod delegation

jcode is DeepSeek-backed and great at single-file edits and explanations.
For repo-scale questions that need a wider view:

```bash
# Ask all harnesses in parallel, summary table
sp ask "find all authentication patterns in this repo"

# Explain a pattern across the codebase
sp explain "JWT validation pattern"

# Run a specific task via a named harness
sp run grok "add tests for auth.py"
sp run omp "review this diff"
```

## Containment

`~/.statepod/omp.json` is shared with prime-agent and Grok. Lean-ctx-style
reads are the containment path for jcode (no PreToolUse hook available).
