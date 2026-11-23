"""Model comparisons preserve all facts while emitting only useful findings."""
from contextlib import redirect_stdout
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
        return walk_bins(self.roots[path], "npiCovStmtBin", self.test, release=self.released.append)


class DifferencesTests(unittest.TestCase):
    def run_analysis(self, session, root, roots=()):
        stage, work = root / "stage", root / "work"
        stage.mkdir(); work.mkdir()
        config = {"metrics": ["line"], "instance_roots": list(roots), "comparison_batch_size": 2}
        with redirect_stdout(io.StringIO()):
            summary, capabilities, report, stats = analyze(session, config, stage, work, SourceMapper(), "input", {})
        return stage, summary, report, stats

    def test_shared_targets_lossless_states_and_only_findings_reported(self):
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
                    {"all_covered": 1, "common_uncovered": 1, "different": 1, "common_special": 1})
                details = list(reader.details())
                self.assertEqual(len(details), 2)  # difference plus explicit special state
                self.assertFalse(any(r["native_locator"][0]["index"] in (0, 1) for r in details))
                holes = list(reader.details("holes"))
                self.assertEqual(len(holes), 3)
                self.assertEqual({r["raw_status"] for r in holes}, {0, 128})
                for row in holes:
                    self.assertEqual(row["target_id"], stable_id(["input", stable_id(["test"]),
                        row["instance_path"], "line", row["native_locator"]]))
                self.assertEqual(len(list(reader.details("common-uncovered"))), 1)
            finally:
                reader.close()
            write_report(stage, {"selected_tests": ["test"]})
            with gzip.open(stage / "differences.jsonl.gz", "rt") as f:
                self.assertEqual(len(list(f)), 2)
            self.assertIn("共同未覆盖", (stage / "report.md").read_text())

    def test_all_covered_emits_no_detail_or_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {p: [(1, 1, 8)] for p in ("top.a", "top.b")})
            stage, _, report, _ = self.run_analysis(session, root)
            self.assertEqual(report["reported_patterns"], 0)
            reader = FactsReader(stage / "coverage_facts.sqlite")
            try:
                self.assertEqual(list(reader.details()), [])
                self.assertEqual(reader.db.execute("SELECT COUNT(*) FROM states").fetchone()[0], 2)
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
            config = {"mode": "differences", "vdb": str(original), "scratch_dir": str(scratch),
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

    def test_cli_mode_defaults_and_rejects_inapplicable_hole_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);vdb = root / "x.vdb";(vdb / "snps/coverage/db").mkdir(parents=True)
            command = ["--mode", "differences", "--vdb", str(vdb), "--out", str(root / "out")]
            config = configuration(parser().parse_args(command))
            self.assertEqual(config["mode"], "differences")
            self.assertEqual(config["work_dir"], tempfile.gettempdir())
            for option in (["--limit", "10"], ["--export-exclusions"], ["--validate-exclusions"],
                           ["--compare-peers", "none"], ["--per-metric"]):
                with self.assertRaises(ToolError):
                    configuration(parser().parse_args(command + option))


if __name__ == "__main__":
    unittest.main()
