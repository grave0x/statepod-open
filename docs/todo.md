# sp todo — 4-Layer Task Manager

> "Every task is a node. Every node has a parent. Every parent has a parent."

sp-todo manages work using a 4-layer schema enforced by invariants.

## Layers

| Layer | Scope | Effort | Rule |
|---|---|---|---|
| **Vision** | The why — outcome, not output | 1 sprint | Max 1 per project |
| **Epic** | Deliverable, days | 1-10 days | Groups related Stories |
| **Story** | 1 commit-cycle | 1-8 hours | 1 Epic parent |
| **Task** | Atomic, ≤4h | minutes-4h | 1 Story parent |

## Quick Start

```bash
sp-todo ls --status todo               # list open tasks
sp-todo add "Fix the login bug" --layer story --priority P1
sp-todo show T-0058                   # show task details
sp-todo start T-0058                 # mark in-progress
sp-todo complete T-0058              # mark done
sp-todo graph                         # show task dependency graph
sp-todo graph --root T-0058          # graph from specific task
sp-todo next                          # next 3 actionable tasks
```

## Subcommands

### `sp-todo add <title>`

Create a task.

```
sp-todo add "Fix auth bug" --layer task --priority P0 --parent T-0001
sp-todo add "Ship API v2" --layer epic --priority P1
```

Flags: `--layer`, `--priority`, `--parent`, `--estimate`, `--due`, `--risk`, `--effort`, `--impact`, `--milestone`, `--assignee`, `--label`

### `sp-todo auto "<goal>"`

Auto-generate a 4-layer chain (Vision → Epic → Story → Task) from a natural language goal.

```
sp-todo auto "I want to build a red team benchmark"
# Creates: T-XXXX Vision → T-YYYY Epic → T-ZZZZ Story → T-WWWW Task
```

### `sp-todo git [--repo PATH] [--count N]`

Create task chains from recent git commits.

```
sp-todo git --repo . --count 10
```

Skips chore/ci/merge/bump commits by default.

### `sp-todo ls [--status STATUS] [--layer LAYER] [--priority P]`

List tasks with optional filters.

```
sp-todo ls --status todo --layer epic
sp-todo ls --priority P0
sp-todo ls --label security
```

### `sp-todo show <id>`

Show task details including description, dependencies, and log.

```
sp-todo show T-0058
```

### `sp-todo start <id>`

Mark a task as in-progress.

```
sp-todo start T-0058
```

### `sp-todo complete <id>`

Mark a task as done.

```
sp-todo complete T-0058
```

### `sp-todo depend <id> <dep-id>...`

Add dependencies to a task.

```
sp-todo depend T-0058 T-0057 T-0056
```

### `sp-todo graph [--fmt ascii|md] [--root ID]`

Render the task dependency graph.

```
sp-todo graph                      # full graph
sp-todo graph --root T-0058      # from specific task
sp-todo graph --fmt md           # Mermaid markdown
```

### `sp-todo next [--limit N]`

Show next actionable tasks (no pending blockers).

```
sp-todo next --limit 5
```

### `sp-todo check [--task ID]`

Verify schema invariants for one or all tasks.

```
sp-todo check                    # check all
sp-todo check --task T-0058     # check one
```

### `sp-todo scaffold "<phrase>"`

Parse a natural-language intent into a layer suggestion.

```
sp-todo scaffold "I want to implement OAuth"
# verb=implement object="OAuth" -> layer=epic
# Run: sp-todo add "OAuth" --layer epic
```

### `sp-todo sync push|pull|status|init <url>`

Git-sync tasks to a remote repo for team collaboration.

```
sp-todo sync status              # show sync status
sp-todo sync init git@github.com:user/statepod-tasks.git
sp-todo sync push               # push to remote
sp-todo sync pull               # pull from remote
```

### `sp-todo serve [--port PORT] [--all-interfaces]`

Serve a JSON HTTP API for external tools.

```
sp-todo serve --port 7741
curl 'http://localhost:7741/?status=todo&layer=epic'
curl http://localhost:7741/T-0058
curl http://localhost:7741/graph
```

### `sp-todo dogfood`

Self-check: creates a test chain, verifies schema, completes, and cleans up.

```
sp-todo dogfood
```

### `sp-todo field ls|show|add`

Manage extension fields (labels, milestone, assignees, due, etc.).

```
sp-todo field ls                 # list all fields
sp-todo field show labels        # show field spec
sp-todo field add effort_score='{"ftype":"int","range":[1,10],"layers":["story","task"]}'
```

## Schema Invariants

| # | Rule |
|---|---|
| 1 | Every **Task** has exactly one **Story** parent |
| 2 | Every **Story** has exactly one **Epic** parent |
| 3 | Every **Epic** has exactly one **Vision** parent |
| 4 | Every **Vision** has no parent |
| 5 | `title` ≤ 80 chars |
| 6 | `description` ≥ 20 chars |
| 7 | **Story** must have acceptance criteria |

## MCP Server

An MCP server providing `statepod_todo_create`, `statepod_todo_list`, and `statepod_todo_show` is installed at:

```
/home/grave/.local/bin/statepod-todo-mcp.py
```

Add to `~/.mcp.json` to use from any MCP client.

## Files

- Task store: `~/.statepod/tasks/<id>.md` (markdown + frontmatter)
- Extension fields: `~/.statepod/task-extensions.json`
- Config: `~/.statepod/config.json`
