#!/usr/bin/env python3
"""Self-test for dag.make_layered_fork_plan(4, 3).

Verifies:
- 4 layers × 3 forks = 12 ops
- schedule() yields 4 layers of 3 ops each
- validate_dag returns ok
- prior-layer ids appear in the next layer's depends
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dag


def main() -> int:
    ops = dag.make_layered_fork_plan(4, 3)
    assert len(ops) == 12, len(ops)

    ok, errs = dag.validate_dag(ops)
    assert ok, errs

    layers = dag.schedule(ops)
    assert [len(L) for L in layers] == [3, 3, 3, 3], layers

    # First layer: no deps. Subsequent: depends on the prior layer's ids.
    for li, layer in enumerate(layers):
        for op in layer:
            oid = op["__id__"]
            assert oid == f"L{li}_F{ops.index(op) % 3}", oid
            if li == 0:
                assert not op["__deps__"]
            else:
                expected = [o["__id__"] for o in layers[li - 1]]
                assert sorted(op["__deps__"]) == sorted(expected), (oid, op["__deps__"], expected)

    # Custom op_factory passes through arbitrary fields
    def fac(li, fi):
        return {"id": f"work_{li}_{fi}", "type": "READ", "path": f"f{li}_{fi}.c"}
    ops2 = dag.make_layered_fork_plan(2, 2, op_factory=fac)
    assert ops2[0]["path"] == "f0_0.c"
    assert ops2[0]["type"] == "READ"
    assert "depends" not in ops2[0]
    assert sorted(ops2[2]["depends"]) == ["work_0_0", "work_0_1"]

    # Edge cases
    ops3 = dag.make_layered_fork_plan(1, 5)
    assert len(ops3) == 5
    layers3 = dag.schedule(ops3)
    assert len(layers3) == 1 and len(layers3[0]) == 5

    try:
        dag.make_layered_fork_plan(0, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

    print(f"OK make_layered_fork_plan(4, 3) — {len(ops)} ops, {len(layers)} layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
