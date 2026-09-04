"""Smoke for build-plan phase 0 maturity bench helpers (no full 42-task run)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator import Orchestrator
from planner import STRAT_MIN_SAMPLES, decide_strategy


class TestMaturityBench(unittest.TestCase):
    def test_strat_min_samples_is_at_least_three(self):
        self.assertGreaterEqual(STRAT_MIN_SAMPLES, 3)

    def test_injected_override_honored_like_bench(self):
        with tempfile.TemporaryDirectory() as td:
            with Orchestrator(td, backend="mock") as orch:
                for _ in range(STRAT_MIN_SAMPLES):
                    orch.state.feedback("STRAT:READ:DELTA", True)
                    orch.state.feedback("STRAT:READ:TARGETED", False)
                picked, _ = decide_strategy(
                    [{"type": "READ", "path": "x.c"}], registry=orch.state)
        self.assertEqual(picked, "DELTA")


if __name__ == "__main__":
    unittest.main()
