# sw todo — 4-Layer Task Manager

> "Every task is a node. Every node has a parent. Every parent has a parent."

sw-todo manages work using a 4-layer schema enforced by invariants.

## Layers

| Layer | Scope | Effort | Rule |
|---|---|---|---|
| **Vision** | The why — outcome, not output | 1 sprint | Max 1 per project |
| **Epic** | Deliverable, days | 1-10 days | Groups related Stories |
| **Story** | 1 commit-cycle | 1-8 hours | 1 Epic parent |
| **Task** | Atomic, ≤4h | minutes-4h | 1 Story parent |

## Quick Start

```bash
sw-todo ls --status todo               # list open tasks
sw-todo add "Fix the login bug" --layer story --priority P1
sw-todo show T-0058                   # show task details
sw-todo start T-0058                 # mark in-progress
sw-todo complete T-0058              # mark done
sw-todo graph                         # show task dependency graph
sw-todo graph --root T-0058          # graph from specific task
sw-todo next                          # next 3 actionable tasks
```

## Subcommands

### `sw-todo add <title>`

Create a task.

```
sw-todo add "Fix auth bug" --layer task --priority P0 --parent T-0001
sw-todo add "Ship API v2" --layer epic --priority P1
```

Flags: `--layer`, `--priority`, `--parent`, `--estimate`, `--due`, `--risk`, `--effort`, `--impact`, `--milestone`, `--assignee`, `--label`

### `sw-todo auto "<goal>"`

Auto-generate a 4-layer chain (Vision → Epic → Story → Task) from a natural language goal.

```
sw-todo auto "I want to build a red team benchmark"
# Creates: T-XXXX Vision → T-YYYY Epic → T-ZZZZ Story → T-WWWW Task
```

### `sw-todo git [--repo PATH] [--count N]`

Create task chains from recent git commits.

```
sw-todo git --repo . --count 10
```

Skips chore/ci/merge/bump commits by default.

### `sw-todo ls [--status STATUS] [--layer LAYER] [--priority P]`

List tasks with optional filters.

```
sw-todo ls --status todo --layer epic
sw-todo ls --priority P0
sw-todo ls --label security
```

### `sw-todo show <id>`

Show task details including description, dependencies, and log.

```
sw-todo show T-0058
```

### `sw-todo start <id>`

Mark a task as in-progress.

```
sw-todo start T-0058
```

### `sw-todo complete <id>`

Mark a task as done.

```
sw-todo complete T-0058
```

### `sw-todo depend <id> <dep-id>...`

Add dependencies to a task.

```
sw-todo depend T-0058 T-0057 T-0056
```

### `sw-todo graph [--fmt ascii|md] [--root ID]`

Render the task dependency graph.

```
sw-todo graph                      # full graph
sw-todo graph --root T-0058      # from specific task
sw-todo graph --fmt md           # Mermaid markdown
```

### `sw-todo next [--limit N]`

Show next actionable tasks (no pending blockers).

```
sw-todo next --limit 5
```

### `sw-todo check [--task ID]`

Verify schema invariants for one or all tasks.

```
sw-todo check                    # check all
sw-todo check --task T-0058     # check one
```

### `sw-todo scaffold "<phrase>"`

Parse a natural-language intent into a layer suggestion.

```
sw-todo scaffold "I want to implement OAuth"
# verb=implement object="OAuth" -> layer=epic
# Run: sw-todo add "OAuth" --layer epic
```

### `sw-todo sync push|pull|status|init <url>`

Git-sync tasks to a remote repo for team collaboration.

```
sw-todo sync status              # show sync status
sw-todo sync init git@github.com:user/swarmstate-tasks.git
sw-todo sync push               # push to remote
sw-todo sync pull               # pull from remote
```

### `sw-todo serve [--port PORT] [--all-interfaces]`

Serve a JSON HTTP API for external tools.

```
sw-todo serve --port 7741
curl 'http://localhost:7741/?status=todo&layer=epic'
curl http://localhost:7741/T-0058
curl http://localhost:7741/graph
```

### `sw-todo dogfood`

Self-check: creates a test chain, verifies schema, completes, and cleans up.

```
sw-todo dogfood
```

### `sw-todo field ls|show|add`

Manage extension fields (labels, milestone, assignees, due, etc.).

```
sw-todo field ls                 # list all fields
sw-todo field show labels        # show field spec
sw-todo field add effort_score='{"ftype":"int","range":[1,10],"layers":["story","task"]}'
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

An MCP server providing `swarmstate_todo_create`, `swarmstate_todo_list`, and `swarmstate_todo_show` is installed at:

```
/home/grave/.local/bin/swarmstate-todo-mcp.py
```

Add to `~/.mcp.json` to use from any MCP client.

## Files

- Task store: `~/.swarmstate/tasks/<id>.md` (markdown + frontmatter)
- Extension fields: `~/.swarmstate/task-extensions.json`
- Config: `~/.swarmstate/config.json`
