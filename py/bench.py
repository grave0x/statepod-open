"""Micro-benchmarks for the SwarmState kernel (Phase 1 spike).

Run:  python3 py/bench.py
Measures kernel-side latency only (no LLM). The orchestrator's task
latency is LLM-dominated; these numbers prove the kernel itself is
sub-millisecond.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from swarmstate import (  # noqa: E402
    SwarmState,
    SS_OP_READ, SS_OP_WRITE, SS_OP_GREP, SS_OP_STATUS,
    SS_CONTEXT_DELTA,
)


def bench(fn, iters=200):
    # warm up
    fn()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    dt = (time.perf_counter() - t0) / iters
    return dt * 1e6  # microseconds


def main():
    with tempfile.TemporaryDirectory(prefix="swarmstate-bench-") as root:
        with SwarmState(root) as s:
            # synthetic repo: 100 files x ~40 lines
            for i in range(100):
                s.write_file(f"src/mod{i:03d}.c",
                             "".join(f"int fn{i}_{j}(int x) {{ return x + {j}; }}\n"
                                     for j in range(40)))

            def pure5():
                s.execute([
                    {"type": SS_OP_WRITE, "path": "tmp_a.txt", "content": "a\n"},
                    {"type": SS_OP_WRITE, "path": "tmp_b.txt", "content": "b\n"},
                    {"type": SS_OP_READ, "path": "src/mod000.c"},
                    {"type": SS_OP_GREP, "pattern": "fn000_10", "target": "src/mod000.c"},
                    {"type": SS_OP_READ, "path": "src/mod001.c"},
                ])

            def with_git_status():
                s.execute([
                    {"type": SS_OP_WRITE, "path": "tmp_a.txt", "content": "a\n"},
                    {"type": SS_OP_WRITE, "path": "tmp_b.txt", "content": "b\n"},
                    {"type": SS_OP_READ, "path": "src/mod000.c"},
                    {"type": SS_OP_GREP, "pattern": "fn000_10", "target": "src/mod000.c"},
                    {"type": SS_OP_STATUS},
                ])

            def delta_read():
                s.read("src/mod050.c", strategy=SS_CONTEXT_DELTA)

            def repo_grep():
                s.grep("fn099_39")

            b = bench(pure5)
            s5 = bench(with_git_status)
            d = bench(delta_read)
            g = bench(repo_grep)
            print(f"5-op batch (2 write/2 read/1 grep): {b:9.1f} us  ({b/5:7.1f} us/op)")
            print(f"5-op batch incl git status spawn  : {s5:9.1f} us")
            print(f"DELTA read (40ln)                 : {d:9.1f} us")
            print(f"GREP whole repo (100 files)       : {g:9.1f} us")
            print(f"-> pure 5-op batch < 1ms          : {'PASS' if b < 1000 else 'FAIL'}")
            print("note: git/execute ops pay a subprocess spawn; the perf")
            print("registry routes those decisions per signature.")


if __name__ == "__main__":
    main()
