"""DAG task graph for the planner.

A plan is a list of ops (nodes) with optional `id` and `depends` fields.
Build a DAG, validate it (no cycles, no missing deps, no self-deps),
and run it with topological scheduling.

Minimal API:
  build_dag(ops) -> {nodes, edges, order, layers, errors}
  validate_dag(ops) -> (ok, errors)
  topo_sort(ops) -> [op_ids]  (raises ValueError on cycle)
  ready(ops, done) -> [ops]   (ops whose deps are all in done)
  schedule(ops) -> [[ops]]    (parallel-friendly layer schedule)
"""
from __future__ import annotations
from collections import defaultdict, deque
from typing import Iterable


def _op_id(op: dict, idx: int) -> str:
    return str(op.get("id") or f"op_{idx}")


def build_dag(ops: list[dict]) -> dict:
    """Build a DAG from ops with optional `id` and `depends` (list of op ids)."""
    nodes = {}  # id -> op
    edges = defaultdict(list)  # dep -> [dependents]
    in_degree = defaultdict(int)
    for i, op in enumerate(ops):
        oid = _op_id(op, i)
        nodes[oid] = op
        deps = op.get("depends") or []
        if isinstance(deps, str):
            deps = [d.strip() for d in deps.split(",") if d.strip()]
        op["__id__"] = oid
        op["__deps__"] = list(deps)
        for d in deps:
            edges[str(d)].append(oid)
            in_degree[oid] += 1
    return {"nodes": nodes, "edges": dict(edges),
            "in_degree": dict(in_degree)}


def validate_dag(ops: list[dict]) -> tuple[bool, list[str]]:
    """Check: no self-deps, no missing deps, no cycles."""
    errors: list[str] = []
    g = build_dag(ops)
    nodes = g["nodes"]
    for oid, op in g["nodes"].items():
        deps = op["__deps__"]
        for d in deps:
            if d == oid:
                errors.append(f"{oid} depends on itself")
            elif d not in nodes:
                errors.append(f"{oid} depends on missing op '{d}'")
    # cycle detection via Kahn's algorithm: count nodes we can remove
    in_deg = defaultdict(int)
    edges = defaultdict(list)
    for oid, op in nodes.items():
        for d in op["__deps__"]:
            if d in nodes and d != oid:
                edges[d].append(oid)
                in_deg[oid] += 1
    queue = deque(n for n in nodes if in_deg[n] == 0)
    visited = 0
    while queue:
        n = queue.popleft()
        visited += 1
        for nxt in edges[n]:
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                queue.append(nxt)
    if visited < len(nodes):
        errors.append(f"cycle detected: {len(nodes) - visited} unreachable op(s)")
    return (not errors, errors)


def topo_sort(ops: list[dict]) -> list[str]:
    """Return op ids in topological order. Raises ValueError on cycle."""
    validate_dag(ops)
    g = build_dag(ops)
    in_deg = defaultdict(int, g["in_degree"])
    edges = defaultdict(list, g["edges"])
    queue = deque(n for n in g["nodes"] if in_deg[n] == 0)
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for nxt in edges[n]:
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(g["nodes"]):
        raise ValueError("cycle in dependency graph")
    return order


def ready(ops: list[dict], done: set[str]) -> list[dict]:
    """Return ops whose dependencies are all in `done`."""
    out = []
    for op in ops:
        oid = op.get("__id__") or _op_id(op, 0)
        deps = set(op.get("__deps__") or [])
        if oid in done:
            continue
        if deps <= done:
            out.append(op)
    return out


def schedule(ops: list[dict]) -> list[list[dict]]:
    """Layered parallel schedule: schedule[i] is the i-th wave of ops."""
    # First ensure all ops have __id__ / __deps__ set
    build_dag(ops)
    layers: list[list[dict]] = []
    done: set[str] = set()
    remaining = list(ops)
    while remaining:
        wave = ready(remaining, done)
        if not wave:
            raise ValueError("no ops ready - cycle or invalid dependencies")
        layers.append(wave)
        for op in wave:
            done.add(op["__id__"])
        remaining = [op for op in remaining if op["__id__"] not in done]
    return layers


def summarize(ops: list[dict]) -> dict:
    """Human-readable summary of the DAG for TUI/log output."""
    try:
        g = build_dag(ops)
        layers = schedule(ops)
        return {
            "ops": len(ops),
            "edges": sum(len(v) for v in g["edges"].values()),
            "layers": len(layers),
            "max_parallel": max(len(l) for l in layers) if layers else 0,
        }
    except Exception as e:
        return {"ops": len(ops), "error": str(e)}


def strip_meta(ops: list[dict]) -> list[dict]:
    """Remove internal __id__/__deps__ keys before returning to caller."""
    out = []
    for op in ops:
        o = {k: v for k, v in op.items()
             if not k.startswith("__")}
        out.append(o)
    return out



def make_layered_fork_plan(layers: int, forks: int, op_factory=None) -> list[dict]:
    """Build a `layers × forks` plan: every layer forks `forks`-way in parallel,
    each layer depends on the entire previous layer.

    op_factory(li, fi) -> dict returns the op for layer li, fork fi.
    Defaults to `{"id": f"L{li}_F{fi}", "type": "OP"}` (caller fills in the
    actual type/path/etc.).
    """
    if layers < 1 or forks < 1:
        raise ValueError("layers and forks must be >= 1")
    if op_factory is None:
        def op_factory(li: int, fi: int) -> dict:
            return {"id": f"L{li}_F{fi}", "type": "OP"}
    ops: list[dict] = []
    prev_ids: list[str] = []
    for li in range(layers):
        layer_ids: list[str] = []
        for fi in range(forks):
            op = op_factory(li, fi)
            if li > 0:
                op["depends"] = list(prev_ids)
            ops.append(op)
            layer_ids.append(op["id"])
        prev_ids = layer_ids
    return ops


if __name__ == "__main__":
    # Self-test
    plan = [
        {"id": "read_a", "type": "READ", "path": "a.c"},
        {"id": "read_b", "type": "READ", "path": "b.c", "depends": ["read_a"]},
        {"id": "diff", "type": "DIFF", "depends": ["read_a", "read_b"]},
        {"id": "report", "type": "WRITE", "path": "out.md", "depends": ["diff"]},
    ]
    ok, errs = validate_dag(plan)
    assert ok, errs
    layers = schedule(plan)
    print("OK schedule:", [len(l) for l in layers], "layers")
    print("  layer 0:", [op["id"] for op in layers[0]])
    print("  layer 1:", [op["id"] for op in layers[1]])
    print("  layer 2:", [op["id"] for op in layers[2]])
    print("  layer 3:", [op["id"] for op in layers[3]])
    # cycle test
    bad = [{"id": "a", "depends": ["b"]}, {"id": "b", "depends": ["a"]}]
    ok, errs = validate_dag(bad)
    assert not ok
    print("OK cycle detected:", errs)
    # missing dep
    bad2 = [{"id": "a", "depends": ["nope"]}]
    ok, errs = validate_dag(bad2)
    assert not ok
    print("OK missing dep:", errs)
    # self dep
    bad3 = [{"id": "a", "depends": ["a"]}]
    ok, errs = validate_dag(bad3)
    assert not ok
    print("OK self dep:", errs)
    print("ALL TESTS PASSED")
