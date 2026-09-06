# SwarmState — Agent Instructions

## Project Overview

SwarmState is a context-containing, state-first C kernel for AI agents. The kernel holds
and exposes project context; the LLM sees only summaries, diffs, and plans.

## 4-Layer Task Schema

Every task is a node in a 4-layer hierarchy:

```
Vision → Epic → Story → Task
```

- **Vision**: The why (outcome, not output). 1 sprint. Max 1 per project.
- **Epic**: Deliverable (days). Groups related Stories.
- **Story**: 1 commit-cycle (1-8 hours). 1 Epic parent.
- **Task**: Atomic (≤4h). 1 Story parent.

## Using sw-todo

Use the task manager for all work tracking. Do not create ad-hoc todos.

```bash
# Natural language → 4-layer scaffold
sw-todo auto "I want to implement the mesh broker"

# Standard workflow
sw-todo add "Fix auth bug" --layer task --priority P1
sw-todo start T-0058
sw-todo complete T-0058

# View work
sw-todo ls --status todo --layer epic
sw-todo graph --root T-0058
sw-todo next  # next actionable tasks
```

## Schema Invariants

Always enforce on task creation:
1. Task → Story → Epic → Vision dependency chain
2. `title` ≤ 80 chars
3. `description` ≥ 20 chars

## MCP Tools

Available via the MCP server at `~/.local/bin/swarmstate-todo-mcp.py`:

- `swarmstate_todo_create(goal, priority)` — auto-generate 4-layer chain
- `swarmstate_todo_list(status, layer, priority, limit)` — list tasks
- `swarmstate_todo_show(task_id)` — show task details

## Kernel

The C kernel (`libswarmstate.so`) provides:
- Context containment via resource tags (`m#:d#:b#`)
- Registry memory (strategy/model/outcome rates per query signature)
- Mesh inference broker for multi-agent coordination

Access via Python:
```python
from swarmstate import SwarmState
s = SwarmState("/path/to/repo")
s.grep("main")  # semantic grep with context
s.plan(query)   # structured plan from query
```

## Governance

Governance is enforced at the planning gate. DENY actions require co-sign.

## File Layout

| Path | What it is |
|------|------------|
| `harness/` | Agent harness: planner, orchestrator, governance |
| `infer/` | Embedded inference handler |
| `py/` | Python bindings + tests |
| `scripts/sw-todo` | Task manager CLI |
| `docs/todo.md` | Full task manager docs |
