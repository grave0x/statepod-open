"""ripgrep GREP backend tests (Phase 3).

Forces SP_GREP_BACKEND=rg so the subprocess path is exercised regardless
of repo size, plus auto-mode checks for the adaptive router.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["SP_GREP_BACKEND"] = "rg"  # must be set before the first kernel call

sys.path.insert(0, str(Path(__file__).resolve().parent))

from statepod import StatePod  # noqa: E402


def make_repo(contents=None):
    d = tempfile.mkdtemp(prefix="statepod-rg-")
    contents = contents or {
        "a.c": "int main(void) { return 0; }\n// TODO: fix me\nALPHA here\n",
        "b.c": "int helper(void) { return 1; }\n// TODO: later\nalpha there\n",
    }
    for name, text in contents.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(name) else None
        with open(p, "w") as f:
            f.write(text)
    return d


class RgBackendTests(unittest.TestCase):
    def test_file_target_format(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("TODO", target="a.c")
        self.assertIn("a.c:2:// TODO: fix me", out)

    def test_repo_wide_format_no_dot_slash(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("TODO")
        self.assertIn("a.c:2:", out)
        self.assertIn("b.c:2:", out)
        self.assertNotIn("./", out)

    def test_case_sensitive_like_c_backend(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("ALPHA")
        self.assertIn("a.c:3:ALPHA here", out)
        self.assertNotIn("alpha there", out)

    def test_anchored_regex(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("^int", target="a.c")
        self.assertIn("a.c:1:int main", out)

    def test_no_matches(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("zzz_nothing_here")
        self.assertIn("no matches", out)

    def test_bad_regex_falls_back_to_c(self):
        d = make_repo()
        with StatePod(d) as ss:
            out = ss.grep("(unclosed")
        # rg exits 2 on bad regex -> kernel falls back to C, which reports the error
        self.assertNotIn("ERROR: rg failed", out)

    def test_cap_limits_results(self):
        d = make_repo({"many.txt": "match\n" * 100})
        with StatePod(d) as ss:
            out = ss.grep("match", target="many.txt", max_results=10)
        self.assertEqual(out.count("many.txt:"), 10)

    def test_auto_mode_large_repo_uses_rg(self):
        os.environ["SP_GREP_BACKEND"] = "auto"
        try:
            d = tempfile.mkdtemp(prefix="statepod-rg-big-")
            for i in range(300):
                with open(os.path.join(d, f"f{i:04d}.c"), "w") as f:
                    f.write(f"int v{i} = {i};\n")
            with StatePod(d) as ss:
                out = ss.grep("v299")
            self.assertIn("f0299.c:1:int v299", out)
        finally:
            os.environ["SP_GREP_BACKEND"] = "rg"


if __name__ == "__main__":
    unittest.main()
