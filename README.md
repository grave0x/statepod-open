# SwarmState (open port)

Context-containing, state-first C kernel for AI agents. The kernel holds
and exposes project context; the LLM sees only summaries, diffs, and plans
(`m#:d#:b#` resource tags), so an agent never has to read — or overflow on —
the full context.

This repository is the **open-source port** of SwarmState. It ships the
AGPL-licensed harness, inference handler, Python bindings, demo scripts,
Windows node, and UI kit, together with the **precompiled proprietary
kernel** (`libswarmstate.so` / `libswarmstate.a`) and its public API header
(`kernel.h`). The kernel source (`kernel.c`) is **not** distributed here.

## Repository layout

| Path | What it is |
|------|------------|
| `kernel.h` | Public C API of the kernel |
| `libswarmstate.so` / `libswarmstate.a` | Precompiled kernel (proprietary — see `KERNEL-LICENSE`) |
| `harness/` | Agent harness: planner, orchestrator, governance, pool, LoRA, identity |
| `infer/` | Embedded inference handler (llama.cpp) |
| `py/` | Python bindings (ctypes) + tests |
| `scripts/` | Mesh, governance, pool, and acceptance demos |
| `win/` | Windows node (C) — mesh daemon + embedded web UI |
| `ui/` | UI Kit spec + role-based widget config |

## Quick start

### Python bindings

The bindings load `libswarmstate.so` from the repository root:

```bash
cd py
python3 - <<'PY'
from swarmstate import SwarmState
with SwarmState("/path/to/some/repo") as s:
    print(s.grep("main"))
PY
```

Set `SWARMSTATE_LIB` to point at a different `libswarmstate.so` if needed.

### Kernel C API

```c
#include "kernel.h"
```

```bash
gcc -O2 mytool.c -I. -L. -lswarmstate -o mytool
```

### Windows node

`win/` holds the node source (`swarmstate-node.c`), the vendored QR
library, the Windows shims, and the cross-compile recipe (`win/build.sh`).
The node links the shared C mesh base and the kernel; see `NOTICE` and
`win/README.txt`.

## Licensing

- **Harness, infer, py, scripts, win node, UI kit** — GNU Affero GPL v3.0
  (`LICENSE`).
- **Kernel binary** (`libswarmstate.so`, `libswarmstate.a`) — proprietary;
  see `KERNEL-LICENSE`.
- **Third-party components** — see `NOTICE`.

## Status

Open-core port for evaluation, field trials, and community feedback. See
the UI Kit spec (`ui/ui-kit-spec-v1.md`) and `win/README.txt` for the
shipped feature surface.


## Task Manager (`sw-todo`)

A 4-layer task manager (Vision → Epic → Story → Task) with CLI, MCP server, and HTTP API.

```bash
sw-todo ls --status todo               # list open tasks
sw-todo auto "I want to build X"      # auto-generate 4-layer chain
sw-todo graph --root T-0058           # dependency graph
sw-todo sync push                     # push to remote git repo
sw-todo serve --port 7741            # HTTP JSON API
sw-todo dogfood                       # self-check (schema invariant verification)

# MCP server: ~/.local/bin/swarmstate-todo-mcp.py
# Docs: docs/todo.md
```

The CLI is also accessible as a `swarmcli todo` subcommand.
