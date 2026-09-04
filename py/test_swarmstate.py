"""SwarmState Python integration tests (Phase 1).

Run:  python3 py/test_swarmstate.py
Requires: libswarmstate.so built (`make`), git on PATH.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from swarmstate import (  # noqa: E402
    SwarmState,
    SS_OP_READ, SS_OP_WRITE, SS_OP_GREP, SS_OP_DIFF, SS_OP_STATUS,
    SS_OP_EXECUTE, SS_OP_AST_PARSE, SS_OP_AST_QUERY,
    SS_CONTEXT_DELTA, SS_CONTEXT_TARGETED, SS_CONTEXT_FULL,
)


class SwarmStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="swarmstate-py-")
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------
    @staticmethod
    def _file_digest(path: bytes, content: bytes) -> bytes:
        return hashlib.sha256(
            path + b"\n" + str(len(content)).encode() + b"\n" + content + b"\n"
        ).digest()

    def test_sha256_matches_hashlib(self):
        """The kernel's state hash must equal hashlib's SHA-256 of the
        documented construction: sorted files (path + file_digest) then the
        bucketed resource tag, which ties plan outcomes to the resource
        conditions they ran under."""
        with SwarmState(self.root) as s:
            s.write_file("abc.txt", "abc")
            s.write_file("empty.txt", "")
            tag = s.resource_tag()
            # sorted paths: abc.txt, empty.txt
            expected = hashlib.sha256(
                b"abc.txt\n" + self._file_digest(b"abc.txt", b"abc") + b"\n"
                + b"empty.txt\n" + self._file_digest(b"empty.txt", b"") + b"\n"
                + b"\nres=" + tag.encode() + b"\n"
            ).hexdigest()
            self.assertEqual(s.state_hash, expected)

    def test_sysinfo_snapshot(self):
        """The kernel owns machine awareness: mem/disk/uptime must be
        populated; battery is optional (desktops)."""
        with SwarmState(self.root) as s:
            si = s.sysinfo()
            self.assertGreater(si["mem_total_kb"], 0)
            self.assertGreater(si["mem_avail_kb"], 0)
            self.assertGreater(si["disk_total_bytes"], 0)
            self.assertGreater(si["disk_free_bytes"], 0)
            self.assertGreater(si["uptime_sec"], 0)
            self.assertGreaterEqual(si["load1"], 0.0)
            if si["battery_pct"] >= 0:
                self.assertIn(si["battery_pct"], range(0, 101))

    def test_resource_tag_shape_and_stability(self):
        import re as _re
        with SwarmState(self.root) as s:
            t1 = s.resource_tag()
            t2 = s.resource_tag()
            self.assertRegex(t1, r"^m[0-2]:d[0-2]:b[clo-]$")
            self.assertEqual(t1, t2)

    def test_registry_timing(self):
        """SNAP-5 temporal registry: min/max/avg/samples tracked, unseen
        signature is all-zeros, and timing survives save/load."""
        with SwarmState(self.root) as s:
            self.assertEqual(s.registry_timing("READ")["samples"], 0)
            s.record_latency("READ", "c", 100)
            s.record_latency("READ", "c", 300)
            s.feedback("READ", True)
            t = s.registry_timing("READ")
            self.assertEqual(t["samples"], 2)
            self.assertEqual(t["min_us"], 100)
            self.assertEqual(t["max_us"], 300)
            self.assertEqual(t["avg_us"], 200)
            self.assertGreater(t["last_ts_ms"], 0)
            snap = os.path.join(self.root, "timing.snap")
            s.save(snap)
        with SwarmState.load(snap) as s2:
            t2 = s2.registry_timing("READ")
            self.assertEqual(t2["samples"], 2)
            self.assertEqual(t2["min_us"], 100)
            self.assertEqual(t2["max_us"], 300)
            self.assertEqual(t2["last_ts_ms"], t["last_ts_ms"])

    def test_journal_hash_snap6(self):
        """SNAP-6 rolling journal hash folds registry + audit events and
        survives save/load."""
        with SwarmState(self.root) as s:
            h1 = s.journal_hash()
            self.assertEqual(len(h1), 64)
            self.assertTrue(all(c in "0123456789abcdef" for c in h1))
            s.record_latency("SIG", "c", 100)
            h2 = s.journal_hash()
            self.assertNotEqual(h1, h2)
            s.feedback("SIG", True)
            h3 = s.journal_hash()
            self.assertNotEqual(h2, h3)
            s.write_file("audit.txt", "content")
            h4 = s.journal_hash()
            self.assertNotEqual(h3, h4)
            snap = os.path.join(self.root, "snap6.bin")
            s.save(snap)
        with SwarmState.load(snap) as s2:
            self.assertEqual(s2.journal_hash(), h4)

    def test_legacy_snap4_loads(self):
        """A SNAP-4 snapshot (no t= line) with several registry entries
        must load without corrupting entry boundaries."""
        import tempfile as _tf
        snap = os.path.join(self.root, "legacy.snap")
        with open(snap, "w") as f:
            f.write("SWARMSTATE-SNAP-4\nroot=" + self.root + "\nserial=0\nckpt=0\nfiles=1\n")
            f.write("path=a.txt\nlen=5\nhello\n")
            f.write("perf=2\n")
            f.write("sig=READ\nkernel=c\ntotal=100\ncount=1\nok=1\nfb=1/1\n")
            f.write("sig=WRITE\nkernel=c\ntotal=50\ncount=1\nok=1\nfb=1/1\n")
        with SwarmState.load(snap) as s:
            self.assertEqual(s.registry_timing("READ")["samples"], 1)
            self.assertEqual(s.registry_timing("WRITE")["samples"], 1)
            self.assertEqual(s.success_rate("READ"), 1.0)

    def test_symbol_summary_extraction_and_filters(self):
        """Documentation layer: functions/structs/enums with signatures,
        call edges, comments; path/pattern/target filters."""
        src = ("// helper: doubles a value\n"
               "static int twice(int v) { return v * 2; }\n\n"
               "/* Parses a config string. */\n"
               "int parse_config(const char* path) { return twice(1); }\n\n"
               "typedef struct { int port; } Config;\n"
               "enum Mode { FAST, SLOW };\n")
        with SwarmState(self.root) as s:
            s.write("src/sample.c", src)
            out = s.symbol_summary(path="src/sample.c")
            self.assertIn("name=parse_config", out)
            self.assertIn("kind=function", out)
            self.assertIn("name=Config", out)
            self.assertIn("kind=struct", out)
            self.assertIn("name=Mode", out)
            self.assertIn("calls=twice", out)          # in-file call edge
            self.assertIn("used_by=parse_config", out)  # reverse edge
            self.assertIn('doc="', out)                 # doc text present
            # filters
            self.assertIn("name=Config", s.symbol_summary(
                path="src/sample.c", target="Config"))
            self.assertNotIn("parse_config", s.symbol_summary(
                path="src/sample.c", target="Config"))
            only_parse = s.symbol_summary(path="src/sample.c", pattern="parse")
            self.assertIn("name=parse_config", only_parse)
            self.assertNotIn("name=twice", only_parse)
            # repo-wide
            self.assertIn("name=parse_config", s.symbol_summary())

    def test_symbol_index_invalidation(self):
        """WRITE invalidates cached summaries; build/invalidate API."""
        with SwarmState(self.root) as s:
            s.write("a.c", "int one(void) { return 1; }\n")
            out = s.symbol_summary(path="a.c")
            self.assertIn("name=one", out)
            self.assertEqual(s.build_symbol_index("a.c"), 1)
            s.invalidate_symbols(["a.c"])
            s.write("a.c", "int two(void) { return 2; }\n")
            out2 = s.symbol_summary(path="a.c")
            self.assertIn("name=two", out2)
            self.assertNotIn("name=one", out2)

    def test_normal_backend_dispatch_full_flag(self):
        """make_plan(backend='normal') must call plan_deepseek with
        full=True (naive baseline); 'deepseek' with full=False."""
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "harness"))
        from unittest import mock as _m
        import planner as _pl
        plan = {"ops": [{"type": "STATUS"}], "strategy": "DELTA"}
        with _m.patch.object(_pl, "plan_deepseek", return_value=plan) as pd:
            _pl.make_plan("q", "s", backend="normal")
            self.assertTrue(pd.call_args.kwargs.get("full"))
            _pl.make_plan("q", "s", backend="deepseek")
            self.assertFalse(pd.call_args.kwargs.get("full"))

    def test_write_read_grep_roundtrip(self):
        with SwarmState(self.root) as s:
            s.write("greptest.txt", "alpha line\nbeta line\nALPHA again\nnothing here\n")
            self.assertEqual(
                s.read("greptest.txt", strategy=SS_CONTEXT_FULL),
                "alpha line\nbeta line\nALPHA again\nnothing here\n",
            )
            m = s.grep("^alpha", target="greptest.txt")
            self.assertIn("greptest.txt:1:alpha line", m)
            self.assertEqual(s.grep("ALPHA"), "greptest.txt:3:ALPHA again\n")

    def test_line_range(self):
        with SwarmState(self.root) as s:
            s.write("f.txt", "l1\nl2\nl3\nl4\n")
            self.assertEqual(s.read("f.txt", 2, 3, SS_CONTEXT_FULL), "l2\nl3\n")
            self.assertEqual(s.read("f.txt", 4, 99, SS_CONTEXT_FULL), "l4\n")

    def test_delta_containment(self):
        """The core claim: DELTA keeps the orchestrator from reading files."""
        with SwarmState(self.root) as s:
            content = "".join(f"line {i} with some words for realism\n" for i in range(200))
            s.write("big.txt", content)
            full = s.read("big.txt", strategy=SS_CONTEXT_FULL)
            delta = s.read("big.txt", strategy=SS_CONTEXT_DELTA)
            self.assertIn("200 lines", delta)
            self.assertLess(len(delta), len(full) // 10)
            # rough token estimate (1 token ~= 4 bytes)
            print(f"\n  [demo] FULL={len(full)}B ~{len(full)//4}t  DELTA={len(delta)}B "
                  f"~{len(delta)//4}t  -> {100 * (1 - len(delta) / len(full)):.1f}% fewer")

    def test_batch_and_touched_files(self):
        with SwarmState(self.root) as s:
            r = s.execute([
                {"type": SS_OP_WRITE, "path": "a.txt", "content": "x"},
                {"type": SS_OP_WRITE, "path": "sub/b.txt", "content": "y"},
                {"type": SS_OP_GREP, "pattern": "y", "target": ""},
            ])
            self.assertTrue(r.ok, r.error_message)
            self.assertEqual(sorted(r.touched_files), ["a.txt", "sub/b.txt"])
            self.assertEqual(r.logs[2], "sub/b.txt:1:y\n")

    def test_path_safety(self):
        with SwarmState(self.root) as s:
            s.write("ok.txt", "safe")
            r = s.execute([{"type": SS_OP_READ, "path": "../escape"}])
            self.assertFalse(r.ok)
            self.assertIn("unsafe path", r.logs[0])
            r = s.execute([{"type": SS_OP_READ, "path": "/etc/passwd"}])
            self.assertFalse(r.ok)
            self.assertIn("unsafe path", r.logs[0])

    def test_execute_whitelist(self):
        with SwarmState(self.root) as s:
            s.write("a.c", "int main(void) { return 0; }\n")
            out = s.execute_cmd("wc -l a.c")
            self.assertIn("1", out)
            r = s.execute([{"type": SS_OP_EXECUTE, "command": "rm -rf /"}])
            self.assertFalse(r.ok)
            self.assertIn("not whitelisted", r.logs[0])

    def test_git_diff_status(self):
        with SwarmState(self.root) as s:
            s.write(".gitignore", ".swarmstate/\n")
            s.write("a.c", "int main(void) { return 0; }\n")
            subprocess.run(["git", "init", "-q", self.root], check=True)
            subprocess.run(["git", "-C", self.root, "add", ".gitignore", "a.c"], check=True)
            s.write("a.c", "int main(void) { return 42; }\n")
            d = s.diff("a.c")
            self.assertIn("+int main(void) { return 42; }", d)
            st = s.status()
            self.assertTrue("a.c" in st and ("M a.c" in st or "AM a.c" in st), st)

    def test_checkpoint_rollback(self):
        with SwarmState(self.root) as s:
            s.write("f.txt", "v1\n")
            ck = s.checkpoint()
            s.write("f.txt", "v2\n")
            self.assertIn("v2", s.read("f.txt"))
            s.rollback(ck)
            self.assertEqual(s.read("f.txt", strategy=SS_CONTEXT_FULL), "v1\n")
            with self.assertRaises(ValueError):
                s.rollback(999999)

    def test_save_load(self):
        snap = os.path.join(self.root, "snap.bin")
        with SwarmState(self.root) as s:
            s.write("keep.txt", "persisted content\n")
            s.save(snap)
        with SwarmState.load(snap) as s2:
            self.assertEqual(s2.read_file("keep.txt"), "persisted content\n")

    def test_ast_escalation(self):
        with SwarmState(self.root) as s:
            s.write("a.c", "int main(void) { return 0; }\n")
            r = s.execute([{"type": SS_OP_AST_PARSE, "path": "a.c"}])
            self.assertTrue(r.ok)
            self.assertFalse(r.needs_escalation)
            self.assertIn("AST a.c:", r.logs[0])
            self.assertIn("functions: 1", r.logs[0])
            self.assertIn("0 errors", r.logs[0])
            # sexp mode
            sexp = s.ast_parse("a.c", mode="sexp")
            self.assertIn("translation_unit", sexp)
            # query mode: find main
            q = s.ast_query(
                "a.c",
                "(function_definition declarator: (function_declarator declarator: (identifier) @name))",
            )
            self.assertIn(":name:identifier:main", q)
            self.assertIn("a.c:1:", q)
            # bad query -> failed op, error text
            bad = s.execute([{"type": SS_OP_AST_QUERY, "path": "a.c", "pattern": "(("}])
            self.assertFalse(bad.ok)
            self.assertIn("ERROR: query", bad.logs[0])

    def test_perf_registry(self):
        with SwarmState(self.root) as s:
            s.record_latency("sig:grep", "c", 100)
            s.record_latency("sig:grep", "c", 200)
            s.record_latency("sig:grep", "rust", 500)
            self.assertEqual(s.best_kernel("sig:grep"), 150.0)
            self.assertEqual(s.best_kernel_name("sig:grep"), "c")
            self.assertEqual(s.best_kernel("sig:none"), 0.0)

    def test_context_targeted(self):
        with SwarmState(self.root) as s:
            content = "\n".join(f"line {i}" for i in range(100)) + "\n"
            s.write("t.txt", content)
            s.write("other.txt", "other\n")
            # TARGETED with only t.txt in target_paths: other.txt must be a summary
            r = s.execute(
                [{"type": SS_OP_READ, "path": "t.txt"}, {"type": SS_OP_READ, "path": "other.txt"}],
                strategy=SS_CONTEXT_TARGETED, target_paths=["t.txt"],
            )
            self.assertIn("line 0", r.logs[0])           # full content
            self.assertIn("other.txt: 1 lines", r.logs[1])  # summary

    def test_loop_stability(self):
        """MVP criterion: 10 consecutive queries without crashing."""
        with SwarmState(self.root) as s:
            s.write("loop.txt", "start\n")
            for i in range(10):
                s.write("loop.txt", f"iteration {i}\n")
                s.read("loop.txt", strategy=SS_CONTEXT_DELTA)
                s.grep("iteration")
            self.assertEqual(s.read_file("loop.txt"), "iteration 9\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
