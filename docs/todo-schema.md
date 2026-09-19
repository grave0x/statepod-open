# 4-Layer Task Schema

> "Every task is a node. Every node has a parent. Every parent has a parent."

## Layers

| Layer | Scope | Effort | Rule |
|---|---|---|---|
| **Vision** | The why — outcome, not output | 1 sprint | Max 1 per project |
| **Epic** | Deliverable, days | 1-10 days | Groups related Stories |
| **Story** | 1 commit-cycle | 1-8 hours | 1 Epic parent |
| **Task** | Atomic, ≤4h | minutes-4h | 1 Story parent |

---

## Schema v1

Every task is stored as:

```
~/.statepod/tasks/<id>.md   ← human-editable, git-trackable
~/.statepod/tasks/index.json ← machine view (task_manager format)
```

### Frontmatter

```yaml
---
id:         T-0001
schema:     todo-schema-v1
layer:      Task
status:     todo
priority:   P0 | P1 | P2 | P3
category:   vision | epic | story | task | infra | research
tags:       [statepod, ffi, docs]
depends:    [T-0001, T-0003]
created:    2026-09-04T00:00:00Z
updated:    2026-09-04T00:00:00Z
source:     https://github.com/grave0x/statepod/issues/42   # optional
---
```

### Body

```markdown
## Title

One sentence. Imperative verb. ≤80 chars.

## Description

≥20 chars. What done looks like. Acceptance criteria if Story layer.

## Log

- 2026-09-04: created
- 2026-09-04: moved to in_progress
```

---

## Invariants (must hold)

| # | Rule | Check |
|---|---|---|
| 1 | Every **Task** has a **Story** parent | `task.depends` contains exactly one Story |
| 2 | Every **Story** has an **Epic** parent | `story.depends` contains exactly one Epic |
| 3 | Every **Epic** has a **Vision** parent | `epic.depends` contains exactly one Vision |
| 4 | Every **Vision** has no parent | `vision.depends` is empty |
| 5 | `title` ≤ 80 chars | length check |
| 6 | `description` ≥ 20 chars | length check |
| 7 | **Story** must have acceptance criteria | body contains `## Acceptance` or `## Done` |

---

## Examples

### Vision → Epic → Story → Task

```
┌─ Vision ──────────────────────────────────────────────────┐
│ "Ship statetui v0.1.0 — statepod orchestrator TUI"    │
└───────────────────────────────────────────────────────────┘
         │
         ▼
┌─ Epic: E1 Layout shell + mode routing ──────────────────┐
│ "3-pane TUI shell, 7 mode switcher, keyboard routing"     │
│ 2-3 days · P0                                           │
└───────────────────────────────────────────────────────────┘
         │
         ▼
┌─ Story: Choose Rust + ratatui stack ───────────────────┐
│ "Pick and record stack decision for E1"                  │
│ Acceptance: stack-decision.md written, T-0020 closed   │
└───────────────────────────────────────────────────────────┘
         │
         ▼
┌─ Task: Write stack-decision.md ──────────────────────────┐
│ "Compare Rust+ratatui vs C99+notcurses, pick one"        │
│ ≤2h · P0                                                 │
└──────────────────────────────────────────────────────────┘
```

### Auto-layer inference (intent parser)

| Agent says | Inferred layer | Notes |
|---|---|---|
| `"I want to build X"` | Vision | outcome is implied |
| `"Ship feature X"` | Epic | deliverable, days |
| `"Add FFI binding"` | Story | 1 commit cycle |
| `"Fix the grep crash"` | Task | atomic, ≤4h |
| `"Research llama.cpp"` | Story | bounded scope |

---

## Layer composition rules

- **Vision** has children that are Epics. Never directly a Task.
- **Epic** has children that are Stories. Never directly a Task.
- **Story** has children that are Tasks. May have 1 Epic grandparent.
- **Task** is a leaf. No children.
- **Parallel stories** in the same Epic may run concurrently.
- **Critical path** is the longest dependency chain from Vision to leaf Task.

---

## CLI Reference

```bash
sp todo add "Title" --layer vision|epic|story|task [--parent T-0001]
sp todo ls    [--layer L] [--status S] [--priority P]
sp todo graph [--fmt ascii|md] [--depth N]
sp todo next  [--limit 3]
sp todo check                      # run invariant checks
sp todo sync push|pull
sp todo scaffold "I want to <verb> <thing>"
```
