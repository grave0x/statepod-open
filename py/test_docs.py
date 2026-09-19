"""sp docs ingestion tests (harness/docs.py)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import docs  # noqa: E402

MD = "# Demo\n\nIntro.\n\n## Install\n\nRun make install.\n" \
     "```sh\n# not a heading\nsw ask\n```\n\n## Usage\n\nsw docs ingest.\n"
RST = "Quickstart\n==========\n\nFast setup.\n\nMesh\n----\n\n" \
      "Lamport clock sync.\n"


def make_repo():
    d = tempfile.mkdtemp(prefix="sp-docs-")
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write(MD)
    with open(os.path.join(d, "docs", "quickstart.rst"), "w") as f:
        f.write(RST)
    os.makedirs(os.path.join(d, ".git"))
    with open(os.path.join(d, ".git", "junk.md"), "w") as f:
        f.write("# skipped\n")
    return d


class DocsIngestTests(unittest.TestCase):
    def test_sections_markdown(self):
        title, secs = docs.split_sections(MD)
        self.assertEqual(title, "Demo")
        self.assertEqual([s[0] for s in secs],
                         ["Demo", "Install", "Usage"])  # fenced, not a heading

    def test_sections_rst(self):
        title, secs = docs.split_sections(RST)
        self.assertEqual(title, "Quickstart")
        self.assertEqual([s[0] for s in secs], ["Quickstart", "Mesh"])

    def test_walk_skips_hidden(self):
        d = make_repo()
        files = [f.name for f in docs.list_docs(d)]
        self.assertEqual(files, ["README.md", "quickstart.rst"])

    def test_ingest_incremental_and_prune(self):
        d = make_repo()
        es, st = docs.ingest(d, out=os.path.join(d, "idx.jsonl"))
        self.assertEqual(st["files"], 2)
        self.assertEqual(st["updated"], 5)
        es2, st2 = docs.ingest(d, out=os.path.join(d, "idx.jsonl"))
        self.assertEqual((st2["updated"], st2["skipped"]), (0, 5))
        self.assertEqual(st2["pruned"], 0)
        os.remove(os.path.join(d, "docs", "quickstart.rst"))
        es3, st3 = docs.ingest(d, out=os.path.join(d, "idx.jsonl"))
        self.assertEqual(st3["pruned"], 2)
        self.assertEqual(len(es3), 3)

    def test_search_prefers_heading_hits(self):
        d = make_repo()
        es, _ = docs.ingest(d, out=os.path.join(d, "idx.jsonl"))
        hits = docs.search(es, "lamport sync")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["heading"], "Mesh")
        self.assertEqual(docs.search(es, "zzzznope"), [])

    def test_line_numbers(self):
        d = make_repo()
        es, _ = docs.ingest(d, out=os.path.join(d, "idx.jsonl"))
        usage = [e for e in es if e["heading"] == "Usage"][0]
        self.assertEqual(usage["line"], MD.splitlines().index("## Usage") + 1)


if __name__ == "__main__":
    unittest.main()
