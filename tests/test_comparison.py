"""Comparison semantics, bounded caching and native handle ownership."""
from collections import Counter
from copy import deepcopy
from contextlib import nullcontext
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_core import Node
from uncover_tool.common import ToolError, descriptor, read_targets, stable_id, state
from uncover_tool.comparison import PeerComparison, SnapshotCache, TargetInventory, compact_state, expand_state


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

    def test_index_failure_does_not_allocate_owned_cache(self):
        session = Session(count=1)
        def broken_info(*args): raise RuntimeError("index failed")
        session.info = broken_info
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison.sqlite"
            with self.assertRaisesRegex(RuntimeError, "index failed"):
                PeerComparison(session, cache_path=path)
            self.assertFalse(path.exists())

    def test_worker_native_failure_closes_cache_and_retains_candidate_inventory(self):
        from uncover_tool import worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, scratch, stage = (root / name for name in ("original.vdb", "scratch", "stage"))
            for directory in (original, scratch, stage): directory.mkdir()
            (original / "fact").write_text("original data")
            session = Session(count=1)
            row = session.row()
            row["start_line"] = 3
            def broken_handle(*args): raise RuntimeError("native read failed")
            session.handle = broken_handle
            def fake_scan(session, config, inventory, mapper, database_id):
                inventory.add(row)
                return {"line": {"uncovered_candidates": 1, "scopes": []}}, {}
            config = {"vdb": str(original), "scratch_dir": str(scratch), "work_dir": str(root),
                      "rtl_roots": [], "path_maps": [], "compare_peers": "selected", "limit": None}
            cwd = Path.cwd()
            try:
                with patch.object(worker, "NativeSession", return_value=nullcontext(session)), \
                     patch.object(worker, "scan", side_effect=fake_scan):
                    with self.assertRaisesRegex(RuntimeError, "native read failed"):
                        worker.run(config, stage)
            finally:
                os.chdir(cwd)
            self.assertFalse((scratch / "comparison.sqlite").exists())
            self.assertTrue((scratch / "inventory.sqlite").exists())
            self.assertEqual((original / "fact").read_text(), "original data")

    def test_unique_results_match_full_results_without_caching_new_groups(self):
        for changed_model in (False, True):
            session = Session(count=3, width=3)
            if changed_model:
                session.models["top.u02"] = "other"
            full = self.comparison(session, cache_entries=0)
            unique = self.comparison(session, cache_entries=0)
            expected = [session.row(index=i) for i in range(3)]
            full.attach_many(expected)
            rows = [session.row(index=i) for i in range(3)]
            self.assertEqual(unique.attach_unique(rows), expected)
            self.assertEqual(unique.cache.db.execute("SELECT COUNT(*) FROM peer_cache").fetchone()[0], 0)
            rows[0]["same_rtl_coverage"]["instances"][0]["flags"]["covered"] = True
            self.assertFalse(rows[1]["same_rtl_coverage"]["instances"][0]["flags"]["covered"])

    def test_unique_skips_equal_display_states_but_reads_and_checks_every_peer(self):
        session = Session(count=2)
        # Independent raw flags differ, but both display as uncovered.
        session.roots["top.u01"].values[7][0].values = Node("0", kind="npiCovStmtBin",
            source="same.sv", raw=1 << 7).values
        comparison = self.comparison(session, cache_entries=0)
        rows = [session.row()]
        self.assertEqual(comparison.attach_unique(rows), [])
        self.assertEqual(session.counts["states"], 2)
        self.assertNotIn("same_rtl_coverage", rows[0])
        self.assertEqual(comparison.stats["all_results_skipped"], 1)
        # Equal statuses cannot hide an incompatible descriptor.
        session.roots["top.u01"] = Node("root", children=[Node("changed")])
        fresh = self.comparison(session)
        row = session.row()
        self.assertEqual(fresh.attach_unique([row]), [row])
        self.assertFalse(row["same_rtl_coverage"]["same_target_differs"])
        self.assertEqual(len(row["same_rtl_coverage"]["incomparable_instances"]), 1)

    def test_unique_reuses_selected_results_and_own_failures_still_abort(self):
        session = Session(count=2)
        session.roots["top.u01"] = Node("root", children=[Node("changed")])
        comparison = self.comparison(session)
        selected = session.row()
        comparison.attach(selected)
        reads = session.counts["states"]
        all_row = session.row()
        self.assertEqual(comparison.attach_unique([all_row]), [selected])
        self.assertEqual(session.counts["states"], reads)
        all_row["same_rtl_coverage"]["instances"][0]["flags"]["covered"] = True
        self.assertFalse(selected["same_rtl_coverage"]["instances"][0]["flags"]["covered"])
        own_bad = session.row()
        own_bad["instance_path"] = "top.u01"
        with self.assertRaises(ToolError): comparison.attach_unique([own_bad])
        fresh = self.comparison(session)
        with self.assertRaises(ToolError): fresh.attach_unique([own_bad])

    def test_scope_snapshot_reuses_selected_and_avoids_fresh_key_queries(self):
        session = Session(count=2, width=5)
        comparison = self.comparison(session, cache_entries=0, batch_size=2)
        comparison.attach(session.row())
        for path in session.instances:
            existing = comparison.snapshot_scope(path, "line")
            before = comparison.cache.counts["state_query_batches"]
            for i, node in enumerate(session.roots[path].values[7]):
                row = session.row(path, i)
                comparison.snapshot(path, "line", row["native_locator"], node, existing=existing, compact=True)
            self.assertEqual(comparison.cache.counts["state_query_batches"], before)
        self.assertEqual(session.counts["states"], 10)
        self.assertEqual(comparison.stats["enumerated_state_hits"], 2)
        # More pre-existing states than the bound safely fall back to lookup.
        self.assertIsNone(comparison.snapshot_scope("top.u00", "line"))
        row = session.row()
        comparison.snapshot("top.u00", "line", row["native_locator"], session.roots["top.u00"].values[7][0])
        self.assertEqual(session.counts["states"], 10)

    def test_scope_snapshot_preserves_cached_error_without_native_reread(self):
        session = Session(count=1)
        comparison = self.comparison(session, cache_entries=0)
        row = session.row()
        locator = stable_id(row["native_locator"])
        key = comparison.state_key(row["instance_path"], "line", locator)
        comparison.cache.put_states({key: {"error": "metric unavailable"}})
        existing = comparison.snapshot_scope(row["instance_path"], "line")
        self.assertEqual(comparison.snapshot(row["instance_path"], "line", row["native_locator"],
            session.roots[row["instance_path"]].values[7][0], existing=existing), {"error": "metric unavailable"})
        self.assertEqual(session.counts["states"], 0)
        with self.assertRaisesRegex(ToolError, "metric unavailable"):
            comparison.attach_unique([row])


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
    def test_result_membership_overflow_falls_back_to_sql(self):
        cache = SnapshotCache(capacity=0, batch_size=2)
        self.addCleanup(cache.close)
        for index in range(3):
            cache.put("result", str(index), {"value": index})
        self.assertIsNone(cache.result_keys)
        self.assertEqual(cache.existing_results(["0", "1", "absent"]),
                         {"0": {"value": 0}, "1": {"value": 1}})

    def test_compact_facts_round_trip_across_batches_and_sql_integer_limits(self):
        cache = SnapshotCache(capacity=0, batch_size=3)
        self.addCleanup(cache.close)
        values = {}
        raw_values = [-1, -99, 0, 1, 2, 4 | 64 | 1, 1023, 1 << 80]
        for i in range(1200):
            key = i % 3, i % 2, i.to_bytes(32, "big")
            facts = compact_state(state(Node("leaf", covered=i % 4 - 1,
                coverable=i % 3, raw=raw_values[i % len(raw_values)]), None))
            values[key] = facts
        error_key = 0, 0, b"error"
        values[error_key] = {"error": "descriptor changed"}
        cache.put_states(values)
        self.assertLess(len(cache.pending_states), 3)
        self.assertEqual(cache.get_states(values), values)
        cache.flush(commit=True)
        self.assertEqual(cache.get_states(values), values)
        self.assertFalse(cache.memory)
        for value in values.values():
            expanded = expand_state(value)
            self.assertEqual(compact_state(expanded), value)
        cache.clear()
        self.assertFalse(cache.get_states(values))

    def test_owned_close_unlinks_without_delete_and_preserves_other_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison.sqlite"
            inventory = sqlite3.connect(Path(tmp) / "inventory.sqlite")
            inventory.execute("CREATE TABLE selected (record TEXT)")
            inventory.execute("INSERT INTO selected VALUES ('kept')")
            cache = SnapshotCache(path=path, batch_size=2)
            statements = []
            cache.db.set_trace_callback(statements.append)
            cache.put_states({(0, 0, b"a"): (0, 1, 0), (0, 0, b"b"): {"error": "no metric"}})
            cache.flush(commit=True)
            cache.close(); cache.close()
            self.assertFalse(path.exists())
            self.assertFalse(any(sql.startswith("DELETE") for sql in statements))
            self.assertEqual(inventory.execute("SELECT * FROM selected").fetchall(), [("kept",)])
            inventory.close()
            path.write_text("caller owned")
            with self.assertRaises(FileExistsError): SnapshotCache(path=path)
            self.assertEqual(path.read_text(), "caller owned")

    def test_shared_close_is_namespace_local_does_not_commit_or_close_connection(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        first, second = SnapshotCache(db=db, capacity=0), SnapshotCache(db=db, capacity=0)
        self.addCleanup(first.close); self.addCleanup(second.close)
        key = 0, 0, b"target"
        first.put_states({key: (0, 1, -1)})
        second.put_states({key: (1, 1, 8)})
        first.flush(); second.flush()
        first.close(); first.close()
        self.assertTrue(db.in_transaction)
        self.assertEqual(second.get_states([key]), {key: (1, 1, 8)})

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
