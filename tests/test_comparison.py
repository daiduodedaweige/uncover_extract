"""Comparison semantics, bounded caching and native handle ownership."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from test_core import Node
from uncover_tool.common import ToolError, descriptor, read_targets, stable_id
from uncover_tool.comparison import PeerComparison, SnapshotCache, TargetInventory


class Session:
    test = None

    def __init__(self, count=16, width=2):
        self.counts = Counter()
        self.instances = {f"top.u{i:02}": Node(str(i), source="same.sv") for i in range(count)}
        self.definitions = {p: {"sources": ["same.sv"]} for p in self.instances}
        self.models = {p: "model" for p in self.instances}
        self.roots = {}
        counts = self.counts
        class CountedNode(Node):
            def covered(self, test):
                counts["states"] += 1
                return super().covered(test)
            def child_handles(self):
                counts["expansions"] += 1
                return super().child_handles()
        for i, path in enumerate(self.instances):
            self.roots[path] = CountedNode("metric", kind="npiCovLineMetric", source="same.sv", children=[
                CountedNode(str(j), kind="npiCovStmtBin", source="same.sv", covered=i % 2) for j in range(width)])
        self.cov = SimpleNamespace(release_handle=lambda node: counts.update(["released"]))

    def handle(self, path, metric): return self.roots[path]
    def info(self, path, metric): return {"model_key": self.models[path]}
    def row(self, path="top.u00", index=0):
        return {"metric": "line", "instance_path": path, "source_file": "same.sv",
                "native_locator": [{"index": index, **descriptor(self.roots[path].values[7][index])}]}


class ComparisonTests(unittest.TestCase):
    def comparison(self, session, **kwargs):
        comparison = PeerComparison(session, metrics=["line"], **kwargs)
        self.addCleanup(comparison.close)
        return comparison

    def test_repeated_model_targets_read_once_and_share_prefixes(self):
        session = Session(width=128)
        comparison = self.comparison(session, profile=True)
        rows = [session.row(path, j) for path in session.instances for j in range(2)]
        comparison.attach_many(rows)
        self.assertEqual(session.counts["states"], 32)
        self.assertEqual(session.counts["expansions"], 16)
        self.assertEqual(session.counts["released"], 16 * 128)
        self.assertEqual(comparison.stats["comparison_hits"], 30)
        self.assertTrue(rows[0]["same_rtl_coverage"]["same_target_differs"])
        self.assertEqual(rows[0]["same_rtl_coverage"], rows[-2]["same_rtl_coverage"])
        rows[0]["same_rtl_coverage"]["instances"][0]["flags"]["covered"] = True
        self.assertFalse(rows[-2]["same_rtl_coverage"]["instances"][0]["flags"]["covered"])

    def test_disk_hits_after_lru_eviction_and_session_isolation(self):
        session = Session(count=2)
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "cache.sqlite")
            first = PeerComparison(session, metrics=["line"], db=db, cache_entries=0)
            row = session.row()
            first.attach(row)
            first.attach(session.row())
            self.assertEqual(session.counts["states"], 2)
            self.assertGreater(first.cache.counts["disk_hits"], 0)
            second = PeerComparison(session, metrics=["line"], db=db, cache_entries=1)
            second.attach(session.row())
            self.assertEqual(session.counts["states"], 4)
            self.assertLessEqual(len(second.cache.memory), 1)
            first.close(); second.close(); db.close()
            with self.assertRaises(RuntimeError): first.attach(session.row())

    def test_cached_peer_failure_remains_fatal_for_own_target(self):
        session = Session(count=2)
        original = session.row()
        session.roots["top.u01"] = Node("metric", children=[Node("changed")])
        comparison = self.comparison(session)
        comparison.attach(original)
        self.assertEqual(original["same_rtl_coverage"]["comparable_instance_count"], 1)
        row = deepcopy(original)
        row["instance_path"] = "top.u01"
        with self.assertRaisesRegex(ToolError, "descriptor changed"):
            comparison.attach(row)

    def test_unknown_model_is_instance_local_and_other_model_is_incomparable(self):
        for model in (None, "different-model"):
            session = Session(count=2)
            session.models["top.u01"] = model
            comparison = self.comparison(session)
            rows = [session.row(path) for path in session.instances]
            comparison.attach_many(rows)
            for row in rows:
                self.assertEqual(row["same_rtl_coverage"]["comparable_instance_count"], 1)
                self.assertEqual(len(row["same_rtl_coverage"]["incomparable_instances"]), 1)
            self.assertEqual(comparison.stats["unique_model_targets"], 2)

    def test_full_locator_and_metric_are_part_of_identity(self):
        session = Session(count=1)
        comparison = self.comparison(session)
        comparison.attach(session.row())
        changed = session.row()
        changed["native_locator"][0]["line"] = 99
        with self.assertRaises(ToolError): comparison.attach(changed)
        other_metric = session.row()
        other_metric["metric"] = "tgl"
        comparison.attach(other_metric)
        self.assertEqual(session.counts["states"], 2)

    def test_source_groups_and_include_source_are_preserved(self):
        session = Session(count=2)
        comparison = self.comparison(session)
        row = session.row()
        row["source_file"] = "included.svh"
        comparison.attach(row)
        self.assertEqual(row["same_rtl_coverage"]["comparable_instance_count"], 2)

    def test_all_inventory_snapshots_avoid_subsequent_native_reads(self):
        session = Session(count=2)
        comparison = self.comparison(session)
        for path in session.instances:
            row = session.row(path)
            comparison.snapshot(path, "line", row["native_locator"], session.roots[path].values[7][0])
        comparison.attach(session.row())
        self.assertEqual(session.counts["states"], 2)
        self.assertEqual(comparison.stats["native_state_reads"], 0)

    def test_unexpected_native_errors_are_not_cached_as_incompatibility(self):
        session = Session(count=1)
        comparison = self.comparison(session)
        def broken_handle(*args): raise RuntimeError("native failed")
        session.handle = broken_handle
        with self.assertRaisesRegex(RuntimeError, "native failed"):
            comparison.attach(session.row())


class BatchOwnershipTests(unittest.TestCase):
    def test_descriptor_and_index_errors_are_local_and_all_handles_released(self):
        children = [Node("0"), Node("1")]
        root = Node("metric", children=children)
        released = []
        targets = {"ok": [{"index": 0, **descriptor(children[0])}],
                   "bad": [{"index": 1, **descriptor(Node("other"))}],
                   "gone": [{"index": 9, **descriptor(children[0])}]}
        result = read_targets(root, targets, None, released.append)
        self.assertEqual(result["ok"]["status"], "uncovered")
        self.assertIn("descriptor changed", result["bad"]["error"])
        self.assertIn("child count changed", result["gone"]["error"])
        self.assertEqual(released, children)

    def test_unexpected_state_exception_releases_children_and_ancestors(self):
        class Broken(Node):
            def covered(self, _): raise RuntimeError("read failed")
        leaf = Broken("leaf")
        branch = Node("branch", children=[leaf])
        sibling = Node("sibling")
        root = Node("root", children=[branch, sibling])
        released = []
        trail = [{"index": 0, **descriptor(n)} for n in (branch, leaf)]
        for strategy in ("auto", "single", "batch"):
            released.clear()
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                read_targets(root, {"x": trail}, None, released.append, strategy=strategy)
            self.assertCountEqual(released, [leaf, branch, sibling])
            self.assertLess(released.index(leaf), released.index(branch))

    def test_individual_and_batch_strategies_preserve_errors_and_handle_ownership(self):
        for strategy in ("single", "batch", "auto"):
            leaf = Node("leaf")
            branch = Node("branch", children=[leaf])
            sibling = Node("sibling")
            root = Node("root", children=[branch, sibling])
            trail = [{"index": 0, **descriptor(n)} for n in (branch, leaf)]
            for target in (trail, [{**trail[0], "name": "changed"}], [{**trail[0], "index": 9}]):
                released, counters = [], Counter()
                result = read_targets(root, {"x": target}, None, released.append, counters, strategy=strategy)
                if target == trail:
                    self.assertEqual(result["x"]["status"], "uncovered")
                    self.assertCountEqual(released, [leaf, branch, sibling])
                else:
                    self.assertIn("error", result["x"])
                    self.assertCountEqual(released, [branch, sibling])
                self.assertEqual(counters["returned_child_handles"], counters["released_child_handles"])


class TargetInventoryTests(unittest.TestCase):
    def test_dedup_across_batches_retains_first_instance_and_original_ids(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        stats = Counter()
        inventory = TargetInventory(db, stats, batch_size=2, identity_capacity=2)
        trail = [{"index": 0, **descriptor(Node("leaf"))}]
        key, locator = inventory.identity("line", "model", "same.sv", trail)
        self.assertEqual(key, stable_id(["line", "model", "same.sv", trail]))
        self.assertEqual(locator, stable_id(trail))
        inventory.add(key, {"instance_path": "first"})
        inventory.add(key, {"instance_path": "duplicate-before-flush"})
        inventory.add("other", {"instance_path": "other"})
        inventory.add(key, {"instance_path": "duplicate-after-flush"})
        inventory.flush()
        import json
        rows = dict(db.execute("SELECT identity,record FROM comparison_targets"))
        self.assertEqual(json.loads(rows[key])["instance_path"], "first")
        self.assertEqual(stats["inventory_records_serialized"], 2)
        self.assertEqual(stats["inventory_duplicate_targets"], 2)
        self.assertFalse(inventory.pending)
        for model, source, target in [("other-model", "same.sv", trail),
                ("model", "other.sv", trail), ("model", "same.sv", [{**trail[0], "name": "changed"}])]:
            self.assertNotEqual(inventory.identity("line", model, source, target)[0], key)
        self.assertLessEqual(len(inventory.identities), 2)


class CacheBatchTests(unittest.TestCase):
    def test_sorted_disk_queries_cross_sql_parameter_batches_and_preserve_namespace(self):
        cache = SnapshotCache(capacity=3)
        self.addCleanup(cache.close)
        for i in range(1200):
            cache.put("state", f"instance/{i:04}", {"value": i})
        values = cache.get_many("state", [f"instance/{i:04}" for i in reversed(range(1200))] + ["absent"])
        self.assertEqual(len(values), 1200)
        self.assertTrue(all(values[f"instance/{i:04}"]["value"] == i for i in range(1200)))
        self.assertLessEqual(len(cache.memory), 3)
        self.assertEqual(cache.get_many("result", ["instance/0000"]), {})


if __name__ == "__main__":
    unittest.main()
