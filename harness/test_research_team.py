#!/usr/bin/env python3
"""Offline self-check for the research-team pipeline (no network, no brains).

Run: python3 harness/test_research_team.py

Covers the model-swap contract:
  1. mechanical (no-brain) run over a local file completes every phase
  2. resume rewrites nothing (done steps are skipped)
  3. marking a phase pending rewrites only that phase (swap-fill)
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research as rh  # noqa: E402


class ResearchTeamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.doc = self.dir / "doc.md"
        self.doc.write_text("Lamport clocks order swarm ops.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mechanical_pipeline_and_swap_semantics(self):
        kits = self.dir / "kits"
        self.assertEqual(rh.main(["team", str(self.doc), "--name",
                                  "teamcheck", "--members", "none",
                                  "--outdir", str(kits)]), 0)
        kit = kits / "teamcheck"
        for f in ["team/state.json", "team/plan.md",
                  "team/teamcheck-t1/finding.md", "team/merge.md"]:
            self.assertTrue((kit / f).is_file(), f)
        st = json.loads((kit / "team" / "state.json").read_text())
        self.assertTrue(st["plan"]["done"] and st["merge"]["done"])
        self.assertTrue(all(t["done"] for t in st["tracks"]))
        self.assertIn("mechanical", st["plan"]["brain"])
        self.assertIn("Research team", (kit / "README.md").read_text())
        p = kit / "team"
        before = {"state": os.path.getmtime(p / "state.json"),
                  "plan": os.path.getmtime(p / "plan.md"),
                  "merge": os.path.getmtime(p / "merge.md"),
                  "finding": os.path.getmtime(
                      p / "teamcheck-t1" / "finding.md")}
        # resume: all done -> nothing rewritten
        self.assertEqual(rh.main(["team", "--kit", str(kit),
                                  "--members", "none"]), 0)
        after = {"state": os.path.getmtime(p / "state.json"),
                 "plan": os.path.getmtime(p / "plan.md"),
                 "merge": os.path.getmtime(p / "merge.md"),
                 "finding": os.path.getmtime(
                     p / "teamcheck-t1" / "finding.md")}
        self.assertEqual(before, after, "resume must not rewrite done steps")
        # swap-fill: pending merge rewrites only merge + state
        st["merge"]["done"] = False
        (p / "state.json").write_text(json.dumps(st))
        self.assertEqual(rh.main(["team", "--kit", str(kit),
                                  "--members", "none"]), 0)
        after2 = {"state": os.path.getmtime(p / "state.json"),
                  "plan": os.path.getmtime(p / "plan.md"),
                  "merge": os.path.getmtime(p / "merge.md"),
                  "finding": os.path.getmtime(
                      p / "teamcheck-t1" / "finding.md")}
        self.assertNotEqual(before["merge"], after2["merge"])
        self.assertNotEqual(before["state"], after2["state"])
        self.assertEqual(before["plan"], after2["plan"])
        self.assertEqual(before["finding"], after2["finding"])

    def test_bundle_from_kit_needs_no_corpus_object(self):
        """The model-swap guarantee: bundle rebuilds purely from files."""
        self.assertEqual(rh.main(["kit", str(self.doc), "--name",
                                  "bundlecheck",
                                  "--outdir", str(self.dir)]), 0)
        bundle = rh.bundle_from_kit(self.dir / "bundlecheck")
        self.assertIn("doc.md", bundle)
        self.assertIn("Lamport clocks", bundle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
