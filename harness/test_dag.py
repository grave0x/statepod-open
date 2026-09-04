#!/usr/bin/env python3
"""Tests for the DAG task graph module.

Run: python3 -m unittest test_dag
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dag  # noqa: E402


class DagTests(unittest.TestCase):

    def test_build_dag_assigns_ids(self):
        ops = [{"type": "READ", "path": "a"}, {"type": "READ", "path": "b"}]
        g = dag.build_dag(ops)
        self.assertEqual(set(g["nodes"].keys()), {"op_0", "op_1"})

    def test_build_dag_uses_explicit_ids(self):
        ops = [{"id": "first", "type": "READ", "path": "a"},
               {"id": "second", "type": "READ", "path": "b"}]
        g = dag.build_dag(ops)
        self.assertEqual(set(g["nodes"].keys()), {"first", "second"})

    def test_validate_dag_no_cycles(self):
        ops = [{"id": "a", "type": "READ", "path": "x", "depends": ["b"]},
               {"id": "b", "type": "READ", "path": "y", "depends": ["c"]},
               {"id": "c", "type": "READ", "path": "z"}]
        ok, errs = dag.validate_dag(ops)
        self.assertTrue(ok, errs)
        self.assertEqual(errs, [])

    def test_validate_dag_detects_cycle(self):
        ops = [{"id": "a", "type": "READ", "path": "x", "depends": ["b"]},
               {"id": "b", "type": "READ", "path": "y", "depends": ["a"]}]
        ok, errs = dag.validate_dag(ops)
        self.assertFalse(ok)
        self.assertTrue(any("cycle" in e for e in errs))

    def test_validate_dag_detects_self_dep(self):
        ops = [{"id": "a", "type": "READ", "path": "x", "depends": ["a"]}]
        ok, errs = dag.validate_dag(ops)
        self.assertFalse(ok)
        self.assertIn("a depends on itself", errs)

    def test_validate_dag_detects_missing_dep(self):
        ops = [{"id": "a", "type": "READ", "path": "x", "depends": ["nope"]}]
        ok, errs = dag.validate_dag(ops)
        self.assertFalse(ok)
        self.assertIn("a depends on missing op 'nope'", errs)

    def test_topo_sort_orders_correctly(self):
        ops = [{"id": "a", "type": "READ", "path": "x"},
               {"id": "b", "type": "READ", "path": "y", "depends": ["a"]},
               {"id": "c", "type": "DIFF", "depends": ["a", "b"]}]
        order = dag.topo_sort(ops)
        self.assertEqual(order.index("a"), 0)
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("b"), order.index("c"))

    def test_schedule_layers_independent_ops_run_first(self):
        ops = [{"id": "a", "type": "READ", "path": "x"},
               {"id": "b", "type": "READ", "path": "y"},
               {"id": "c", "type": "DIFF", "depends": ["a", "b"]}]
        layers = dag.schedule(ops)
        # First layer: a, b (parallel)
        # Second layer: c
        self.assertEqual(len(layers), 2)
        self.assertEqual(set(op["id"] for op in layers[0]), {"a", "b"})
        self.assertEqual([op["id"] for op in layers[1]], ["c"])

    def test_ready_filters_by_done(self):
        ops = [{"id": "a", "type": "READ", "path": "x"},
               {"id": "b", "type": "READ", "path": "y", "depends": ["a"]}]
        dag.build_dag(ops)  # assign __id__
        ready = dag.ready(ops, done=set())
        self.assertEqual([op["id"] for op in ready], ["a"])
        ready2 = dag.ready(ops, done={"a"})
        self.assertEqual([op["id"] for op in ready2], ["b"])

    def test_summarize_metrics(self):
        ops = [{"id": "a", "type": "READ", "path": "x"},
               {"id": "b", "type": "READ", "path": "y"},
               {"id": "c", "type": "DIFF", "depends": ["a", "b"]}]
        s = dag.summarize(ops)
        self.assertEqual(s["ops"], 3)
        self.assertEqual(s["edges"], 2)
        self.assertEqual(s["layers"], 2)
        self.assertEqual(s["max_parallel"], 2)

    def test_string_depends_parsed_as_csv(self):
        ops = [{"id": "a"}, {"id": "b"}, {"id": "c", "depends": "a, b"}]
        ok, errs = dag.validate_dag(ops)
        self.assertTrue(ok, errs)
        layers = dag.schedule(ops)
        self.assertEqual(len(layers), 2)
        self.assertEqual(set(op["id"] for op in layers[0]), {"a", "b"})

    def test_strip_meta_removes_internal_keys(self):
        ops = [{"id": "a", "type": "READ", "path": "x"}]
        dag.build_dag(ops)
        clean = dag.strip_meta(ops)
        self.assertNotIn("__id__", clean[0])
        self.assertNotIn("__deps__", clean[0])
        self.assertEqual(clean[0]["id"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
