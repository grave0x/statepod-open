# StatePod (open port)

Context-containing, state-first C kernel for AI agents. The kernel holds
and exposes project context; the LLM sees only summaries, diffs, and plans
(`m#:d#:b#` resource tags), so an agent never has to read — or overflow on —
the full context.

This repository is the **open-source port** of StatePod. It ships the
AGPL-licensed harness, inference handler, Python bindings, demo scripts,
Windows node, and UI kit, together with the **precompiled proprietary
kernel** (`libstatepod.so` / `libstatepod.a`) and its public API header
(`kernel.h`). The kernel source (`kernel.c`) is **not** distributed here.

## Repository layout

| Path | What it is |
|------|------------|
| `kernel.h` | Public C API of the kernel |
| `libstatepod.so` / `libstatepod.a` | Precompiled kernel (proprietary — see `KERNEL-LICENSE`) |
| `harness/` | Agent harness: planner, orchestrator, governance, pool, LoRA, identity |
| `infer/` | Embedded inference handler (llama.cpp) |
| `py/` | Python bindings (ctypes) + tests |
| `scripts/` | Mesh, governance, pool, and acceptance demos |
| `win/` | Windows node (C) — mesh daemon + embedded web UI |
| `ui/` | UI Kit spec + role-based widget config |

## Quick start

### Python bindings

The bindings load `libstatepod.so` from the repository root:

```bash
cd py
python3 - <<'PY'
from statepod import StatePod
with StatePod("/path/to/some/repo") as s:
    print(s.grep("main"))
PY
```

Set `STATEPOD_LIB` to point at a different `libstatepod.so` if needed.

### Kernel C API

```c
#include "kernel.h"
```

```bash
gcc -O2 mytool.c -I. -L. -lstatepod -o mytool
```

### Windows node

`win/` holds the node source (`statepod-node.c`), the vendored QR
library, the Windows shims, and the cross-compile recipe (`win/build.sh`).
The node links the shared C mesh base and the kernel; see `NOTICE` and
`win/README.txt`.

## Licensing

- **Harness, infer, py, scripts, win node, UI kit** — GNU Affero GPL v3.0
  (`LICENSE`).
- **Kernel binary** (`libstatepod.so`, `libstatepod.a`) — proprietary;
  see `KERNEL-LICENSE`.
- **Third-party components** — see `NOTICE`.

## Status

Open-core port for evaluation, field trials, and community feedback. See
the UI Kit spec (`ui/ui-kit-spec-v1.md`) and `win/README.txt` for the
shipped feature surface.


## Task Manager (`sp-todo`)

A 4-layer task manager (Vision → Epic → Story → Task) with CLI, MCP server, and HTTP API.

```bash
sp-todo ls --status todo               # list open tasks
sp-todo auto "I want to build X"      # auto-generate 4-layer chain
sp-todo graph --root T-0058           # dependency graph
sp-todo sync push                     # push to remote git repo
sp-todo serve --port 7741            # HTTP JSON API
sp-todo dogfood                       # self-check (schema invariant verification)

# MCP server: ~/.local/bin/statepod-todo-mcp.py
# Docs: docs/todo.md
```

The CLI is also accessible as a `statepod todo` subcommand.
