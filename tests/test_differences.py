"""Model comparisons preserve all facts while emitting only useful findings."""
from contextlib import redirect_stdout, redirect_stderr
from contextlib import nullcontext
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_core import Node
from uncover_tool.cli import parser, configuration
from uncover_tool.common import ToolError, stable_id, walk_bins
from uncover_tool.differences import FactsReader, FactStore, analyze, write_report
from uncover_tool.mapping import SourceMapper


class Session:
    test = None
    selected_tests = ["test"]

    def __init__(self, root, states, models=None, names=None):
        self.vdb = root
        self.instances = {p: Node(p, source="same.sv") for p in states}
        self.definitions = {p: {"sources": ["same.sv"]} for p in states}
        self.models = models or {p: "model" for p in states}
        self.roots = {}
        for p, values in states.items():
            leaves = [Node((names or {}).get(p, {}).get(i, str(i)), kind="npiCovStmtBin", source="same.sv",
                           covered=v[0], coverable=v[1], raw=v[2]) for i, v in enumerate(values)]
            self.roots[p] = Node("metric", kind="npiCovLineMetric", source="same.sv", children=leaves,
                                 covered=sum(v[0] for v in values), coverable=sum(v[1] for v in values))
        self.released = []
        self.cov = type("Cov", (), {"release_handle": self.released.append})()

    def handle(self, path, metric):
        return self.roots[path] if metric == "line" else None

    def info(self, path, metric):
        return {"model_key": self.models[path], "module_name": "Same"}

    def bins(self, path, metric):
        return walk_bins(self.roots[path], "npiCovStmtBin", release=self.released.append)


class DifferencesTests(unittest.TestCase):
    def run_analysis(self, session, root, roots=(), metrics=("line",)):
        stage, work = root / "stage", root / "work"
        stage.mkdir(); work.mkdir()
        config = {"metrics": list(metrics), "instance_roots": list(roots), "batch_size": 2}
        with redirect_stdout(io.StringIO()):
            summary, capabilities, report, stats = analyze(session, config, stage, work, SourceMapper(), "input", {})
        return stage, summary, report, stats

    def test_shared_targets_preserve_included_states_and_only_findings_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Same display status with different flags remains a common hole.
            session = Session(root, {"top.a": [(1, 1, 8), (0, 1, 0), (0, 1, 0), (0, 0, 4)],
                                     "top.b": [(1, 1, 8), (0, 1, 128), (1, 1, 8), (0, 0, 4)]})
            stage, summary, report, stats = self.run_analysis(session, root)
            self.assertEqual(stats["native_atomic_state_reads"], 8)
            self.assertEqual(stats["target_descriptions_serialized"], 4)
            self.assertEqual(stats["verified_reference_matches"], 4)
            self.assertEqual(report["targets_with_state_differences"], 1)
            self.assertEqual(summary["line"]["uncovered_candidates"], 3)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(dict(reader.db.execute("SELECT category,COUNT(*) FROM classifications GROUP BY category")),
                    {"all_covered": 1, "common_uncovered": 1, "different": 1})
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM states").fetchone()[0], 6)
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0], 3)
                details = list(reader.details())
                self.assertEqual(len(details), 1)
                self.assertFalse(any(r["native_locator"][0]["index"] in (0, 1) for r in details))
                holes = list(reader.details("holes"))
                self.assertEqual(len(holes), 3)
                self.assertEqual({r["raw_status"] for r in holes}, {0, 128})
                for row in holes:
                    self.assertEqual(row["target_id"], stable_id(["input", stable_id(["test"]),
                        row["instance_path"], "line", row["native_locator"]]))
                self.assertEqual(len(list(reader.details("common-uncovered"))), 1)
                self.assertEqual(reader.uncovered_instances(), [
                    {"module_name": "Same", "instance_path": "top.a", "uncovered_count": 2},
                    {"module_name": "Same", "instance_path": "top.b", "uncovered_count": 1}])
            finally:
                reader.close()
            write_report(stage, {"selected_tests": ["test"]})
            with gzip.open(stage / "differences.jsonl.gz", "rt") as f:
                self.assertEqual(len(list(f)), 1)
            text = (stage / "report.md").read_text()
            self.assertIn("| 模块 | 实例 | 未覆盖点数 |", text)
            self.assertIn("| Same | `top.a` | 2 |", text)
            self.assertIn("| Same | `top.b` | 1 |", text)
            self.assertNotIn("| 模型组 |", text)
            self.assertIn("## 分类依据", text)
            self.assertNotIn("已排除", text)

    def test_all_excluded_points_leave_no_analysis_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 0, 4), (0, 0, 64), (0, 0, 6)],
                                     "top.b": [(0, 0, 4), (0, 0, 64), (0, 0, 6)]})
            stage, summary, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["unique_model_targets_checked"], 0)
            self.assertEqual(report["reported_patterns"], 0)
            self.assertEqual(summary["line"]["visited_atomic_bins"], 0)
            self.assertNotIn("visited_excluded", summary["line"])
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                for table in ("states", "targets", "classifications", "patterns", "findings"):
                    self.assertEqual(reader.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
                for kind in ("differences", "holes", "common-uncovered"):
                    self.assertEqual(list(reader.details(kind)), [])
            finally:
                reader.close()
            write_report(stage, {})
            self.assertEqual(json.loads((stage / "module_summary.json").read_text())["models"], [])
            with gzip.open(stage / "difference_patterns.jsonl.gz", "rt") as stream:
                self.assertEqual(stream.read(), "")

    def test_instance_exclusions_do_not_create_holes_differences_or_incomparables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 0, 4), (0, 1, 0), (0, 0, 64), (1, 1, 8)],
                                     "top.b": [(0, 1, 0), (0, 0, 64), (0, 0, 4), (1, 1, 8)]})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["unique_model_targets_checked"], 3)
            self.assertEqual(report["targets_with_state_differences"], 0)
            self.assertEqual(report["targets_with_incomparable_instances"], 0)
            self.assertEqual(report["reported_patterns"], 0)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(reader.uncovered_instances(), [
                    {"module_name": "Same", "instance_path": "top.a", "uncovered_count": 1},
                    {"module_name": "Same", "instance_path": "top.b", "uncovered_count": 1}])
                self.assertEqual(len(list(reader.details("holes"))), 2)
                common = list(reader.details("common-uncovered"))
                self.assertEqual(len(common), 2)
                self.assertTrue(all(not row["excluded"] for row in common))
                self.assertTrue(all(len(row["same_rtl_coverage"]["instances"]) == 1 for row in common))
            finally:
                reader.close()

    def test_outside_peers_do_not_reintroduce_excluded_selected_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 0, 4)], "top.b": [(0, 1, 0)]})
            stage, _, report, _ = self.run_analysis(session, root, ["top.a"])
            self.assertEqual(report["unique_model_targets_checked"], 0)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(reader.db.execute("SELECT selected FROM targets").fetchall(), [(0,)])
                self.assertEqual(list(reader.details("holes")), [])
                self.assertEqual(reader.uncovered_instances(), [])
            finally:
                reader.close()

    def test_exclusions_are_absent_from_remaining_difference_distributions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 0, 4)], "top.b": [(0, 1, 0)], "top.c": [(1, 1, 8)]})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["targets_with_state_differences"], 1)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                comparison = list(reader.details())[0]["same_rtl_coverage"]
                self.assertEqual([row["instance_path"] for row in comparison["instances"]], ["top.b", "top.c"])
                self.assertEqual(comparison["incomparable_instances"], [])
                self.assertTrue(comparison["same_target_differs"])
            finally:
                reader.close()
            write_report(stage, {})
            text = (stage / "report.md").read_text()
            self.assertNotIn("top.a", text)
            self.assertIn("| `top.b` | 未覆盖 |", text)
            self.assertIn("| `top.c` | 已覆盖 |", text)
            with gzip.open(stage / "difference_patterns.jsonl.gz", "rt") as stream:
                distribution = json.loads(stream.readline())["instances"]
                self.assertEqual([entry["status"] for entry in distribution], ["uncovered", "covered"])

    def test_all_covered_emits_no_detail_or_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {p: [(1, 1, 8)] for p in ("top.a", "top.b")})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["reported_patterns"], 0)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(list(reader.details()), [])
                self.assertEqual(reader.uncovered_instances(), [])
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM states").fetchone()[0], 2)
            finally:
                reader.close()
            write_report(stage, {"selected_tests": ["test"]})
            self.assertIn("没有发现未覆盖的模块实例", (stage / "report.md").read_text())

    def test_uncovered_instances_merge_metrics_and_ignore_special_only_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 1, 0), (1, 1, 8), (0, 1, 4)],
                                     "top.b": [(0, 1, 0), (0, 1, 0), (0, 1, 4)],
                                     "top.c": [(1, 1, 8), (1, 1, 8), (0, 1, 4)]})
            session.handle = lambda path, metric: session.roots[path] if metric in ("line", "tgl") else None
            stage, _, _, _ = self.run_analysis(session, root, metrics=("line", "tgl"))
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(reader.uncovered_instances(), [
                    {"module_name": "Same", "instance_path": "top.a", "uncovered_count": 2},
                    {"module_name": "Same", "instance_path": "top.b", "uncovered_count": 4}])
            finally:
                reader.close()

    def test_equal_module_percentages_do_not_hide_different_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 1, 0), (1, 1, 8)],
                                     "top.b": [(1, 1, 8), (0, 1, 0)]})
            _, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["targets_with_state_differences"], 2)

    def test_selected_extra_targets_are_included_in_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 1, 0)], "top.b": [(0, 1, 0), (1, 1, 8)]})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["unique_model_targets_checked"], 2)
            self.assertEqual(report["targets_with_incomparable_instances"], 1)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                row = list(reader.details())[0]
                self.assertEqual(row["instance_path"], "top.b")
                self.assertEqual(row["same_rtl_coverage"]["instances"][0]["status"], "covered")
            finally:
                reader.close()

    def test_worker_failure_audits_input_and_closes_partial_facts(self):
        from uncover_tool import worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, scratch, stage = [root / x for x in ["original.vdb", "scratch", "stage"]]
            for p in (original, scratch, stage):p.mkdir()
            (original / "fact").write_text("original")
            session = Session(original, {"top.a": [(0, 1, 0)]})
            def broken_bins(*args):
                raise RuntimeError("native read failed")
            session.bins = broken_bins
            config = {"vdb": str(original), "scratch_dir": str(scratch),
                      "work_dir": str(root), "rtl_roots": [], "path_maps": [], "metrics": ["line"], "instance_roots": []}
            cwd = Path.cwd()
            try:
                with patch.object(worker, "NativeSession", return_value=nullcontext(session)):
                    with self.assertRaisesRegex(RuntimeError, "native read failed"):
                        worker.run(config, stage)
            finally:
                os.chdir(cwd)
            self.assertTrue(json.loads((stage / "input_audit.json").read_text())["original_unchanged"])
            self.assertFalse((stage / "coverage_facts.sqlite").exists())
            # An exclusive transaction succeeds: no analysis connection leaked.
            db = sqlite3.connect(scratch / "coverage_facts.sqlite", timeout=0)
            try:db.execute("BEGIN EXCLUSIVE")
            finally:db.close()

    def test_scope_keeps_outside_peers_but_not_their_extra_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 1, 0)], "top.b": [(1, 1, 8), (0, 1, 0)]})
            stage, summary, report, _ = self.run_analysis(session, root, ["top.a"])
            self.assertEqual(summary["line"]["uncovered_candidates"], 1)
            self.assertEqual(report["unique_model_targets_checked"], 1)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(len(list(reader.details())[0]["same_rtl_coverage"]["instances"]), 2)
                self.assertEqual([r["instance_path"] for r in reader.details("holes")], ["top.a"])
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0], 2)
                self.assertEqual(reader.uncovered_instances(), [
                    {"module_name": "Same", "instance_path": "top.a", "uncovered_count": 1}])
            finally:
                reader.close()

    def test_descriptor_mismatch_and_missing_nodes_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {"top.a": [(0, 1, 0), (0, 1, 0)], "top.b": [(0, 1, 0)]},
                              names={"top.b": {0: "changed"}})
            stage, _, report, stats = self.run_analysis(session, root)
            self.assertEqual(stats["reference_mismatches"], 1)
            self.assertEqual(report["unique_model_targets_checked"], 3)
            self.assertEqual(report["targets_with_incomparable_instances"], 3)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertTrue(all(r["same_rtl_coverage"]["incomparable_instances"] for r in reader.details()))
                self.assertEqual(len(list(reader.details("holes"))), 3)
                self.assertEqual(reader.uncovered_instances(), [
                    {"module_name": "Same", "instance_path": "top.a", "uncovered_count": 2},
                    {"module_name": "Same", "instance_path": "top.b", "uncovered_count": 1}])
            finally:
                reader.close()

    def test_unknown_and_different_models_are_never_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {p: [(0, 1, 0)] for p in ("top.a", "top.b", "top.c")},
                              models={"top.a": None, "top.b": None, "top.c": "other"})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["unique_model_targets_checked"], 3)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                for row in reader.details():
                    self.assertEqual(row["same_rtl_coverage"]["comparable_instance_count"], 1)
                    self.assertEqual(len(row["same_rtl_coverage"]["incomparable_instances"]), 2)
            finally:
                reader.close()

    def test_large_and_negative_native_values_round_trip_without_sqlite_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FactStore(Path(tmp) / "facts.sqlite", batch_size=1)
            try:
                store.add_state(1, 1, (-99, 1 << 80, 1 << 81))
                store.flush(commit=True)
                self.assertEqual(store.states([1], [1]), {1: {1: (-99, 1 << 80, 1 << 81)}})
            finally:
                store.close()

    def test_default_cli_uses_fact_analysis_and_local_scratch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);vdb = root / "x.vdb";(vdb / "snps/coverage/db").mkdir(parents=True)
            command = ["--vdb", str(vdb), "--out", str(root / "out")]
            config = configuration(parser().parse_args(command))
            self.assertNotIn("mode", config)
            self.assertEqual(config["work_dir"], tempfile.gettempdir())
            self.assertEqual(config["batch_size"], 256)
            for option in (["--mode", "differences"], ["--mode", "holes"], ["--limit", "10"],
                           ["--limit-per-metric", "10"], ["--export-exclusions"], ["--validate-exclusions"],
                           ["--compare-peers", "selected"], ["--comparison-cache-entries", "1024"],
                           ["--comparison-batch-size", "256"], ["--profile-comparison"],
                           ["--include-other-states"], ["--per-metric"]):
                with self.subTest(option=option), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser().parse_args(command + option)

    def test_default_worker_publishes_full_facts_without_candidate_inventory(self):
        from uncover_tool import worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vdb, stage, scratch = root / "input.vdb", root / "stage", root / "scratch"
            (vdb / "snps/coverage/db").mkdir(parents=True)
            (vdb / ".vdb_version").write_text("test-version")
            stage.mkdir(); scratch.mkdir()
            config = configuration(parser().parse_args(["--vdb", str(vdb), "--out", str(root / "out"),
                                                       "--metrics", "line", "--batch-size", "1"]))
            config["scratch_dir"] = str(scratch)
            session = Session(vdb, {"top.a": [(0, 1, 0), (1, 1, 8)], "top.b": [(1, 1, 8), (1, 1, 8)]})
            session.available_tests, session.warnings = ["test"], []
            cwd = Path.cwd()
            try:
                with patch.object(worker, "NativeSession", return_value=nullcontext(session)), redirect_stdout(io.StringIO()):
                    worker.run(config, stage)
            finally:
                os.chdir(cwd)
            manifest = json.loads((stage / "analysis_manifest.json").read_text())
            self.assertEqual(manifest["schema_version"], "2.0")
            self.assertTrue(manifest["input"]["original_unchanged"])
            self.assertEqual(manifest["summary"]["line"]["uncovered_candidates"], 1)
            self.assertEqual(manifest["same_rtl_comparison"]["targets_with_state_differences"], 1)
            self.assertEqual(manifest["performance"]["comparison"]["native_atomic_state_reads"], 4)
            self.assertFalse((scratch / "inventory.sqlite").exists())
            self.assertFalse((scratch / "comparison.sqlite").exists())
            self.assertFalse((stage / "uncovered_coverage.json").exists())
            self.assertTrue((stage / "report.md").exists())
            self.assertTrue((stage / "differences.csv").exists())
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(len(list(reader.details("holes"))), 1)
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM states").fetchone()[0], 4)
            finally:
                reader.close()


if __name__ == "__main__":
    unittest.main()
