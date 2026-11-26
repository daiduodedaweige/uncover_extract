"""Observable identity, publication and elaboration invariants; no proprietary tools required."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
from runtime import (ToolError, analysis_dir, begin, digest_file, read_json,
                     project_root, record_query, resolve, stable_id, write_json)
from uncover_extract.facts import FactStore, packed
from uncover_extract.output import source_cells
from coverage_query import describe_goal, related
from analysis_report import finalize, render_existing, render_markdown
from design_query import correspondence


def fixture(root):
    (root / ".git").mkdir(exist_ok=True)
    result = root / "result"
    result.mkdir()
    source = root / "logic.sv"
    shutil.copy2(SKILL / "tests/logic.sv", source)
    mapping = {"status": "resolved", "path": str(source), "sha256": digest_file(source)}
    store = FactStore(result / "coverage_facts.sqlite")
    metadata = {"schema_version": "2.0", "database_id": "db-identity", "test_scope_ref": stable_id(["mode0", "mode1"]),
                "selected_tests": ["mode0", "mode1"], "configuration": {"metrics": ["line", "tgl", "branch", "cond"], "vdb": str(root / "absent.vdb")}}
    store.db.executemany("INSERT INTO metadata VALUES (?,?)", ((k, packed(v)) for k, v in metadata.items()))
    store.db.execute("INSERT INTO instances VALUES (1,'top.u',1,'leaf')")
    store.db.execute("INSERT INTO sources VALUES (1,?,?)", (str(source), packed(mapping)))
    metrics = {}
    for mid, metric in enumerate(("line", "tgl", "branch", "cond"), 1):
        store.metrics[mid] = metric
        store.db.execute("INSERT INTO models VALUES (?,?,?,?)", (mid, metric, metric, packed({"module_name": "leaf"})))
        tids = []
        for ordinal in range(2):
            if metric == "line":
                trail = [{"index": ordinal, "type": "npiCovStmtBin", "name": "8", "source_file": str(source), "line": 8}]
            elif metric == "tgl":
                trail = [{"index": 0, "type": "npiCovSignalBit", "name": "q[0]", "source_file": str(source), "line": 3},
                         {"index": ordinal, "type": "npiCovToggleBin", "name": ["0 -> 1", "1 -> 0"][ordinal], "source_file": None, "line": None}]
            else:
                trail = [{"index": 0, "type": "npiCovBranch" if metric == "branch" else "npiCovCondition", "name": "en", "source_file": str(source), "line": 9},
                         {"index": ordinal, "type": "npiCovBranchBin" if metric == "branch" else "npiCovConditionBin", "name": ["01", "10"][ordinal], "source_file": None, "line": None}]
            tid = store.add_target(mid, ordinal, 1, 8 if metric == "line" else 3 if metric == "tgl" else 9, trail, 1, True)
            store.add_state(1, tid, (0, 1, 128 if ordinal else 0))
            tids.append(tid)
        store.db.execute("INSERT INTO scopes VALUES (?,?,?,?,?)", (mid, 1, 0, 2, 2))
        first = (mid - 1) * 2 + 1
        page = result / f"{metric}.md"
        records = []
        for ordinal, tid in enumerate(tids):
            line = 8 if metric == "line" else 3 if metric == "tgl" else 9
            store.flush()
            description = store.db.execute("SELECT description FROM targets WHERE id=?", (tid,)).fetchone()[0]
            records.append(f"- [ ] 编号：UC-{first + ordinal:06d}；源码文件：{source_cells(str(source), None, mapping, result)[0]}；行号：{line}" + (f"；信号 / 覆盖点 / 方向：{description}" if metric != "line" else ""))
        page.write_text("# top.u\n\n" + "\n".join(records) + "\n", encoding="utf-8")
        metrics[metric] = {"status": "analyzed", "uncovered_count": 2, "atomic_count": 2, "pages": [{"path": page.name, "records": 2, "first_point_no": first, "last_point_no": first + 1}]}
    store.flush(commit=True)
    store.close()
    write_json(result / "instance_summary.json", {"detail_format": "numbered_checklist_v1", "instances": [{"instance_id": 1, "instance_path": "top.u", "module_name": "leaf", "metrics": metrics}]})
    return result, source


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.result, self.source = fixture(self.root)

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temp.cleanup()

    def test_unique_native_identity_on_same_physical_line(self):
        a, b = [resolve(self.result, f"UC-{n:06d}") for n in (1, 2)]
        self.assertEqual(a["target"]["start_line"], b["target"]["start_line"])
        self.assertNotEqual(a["target"]["target_id"], b["target"]["target_id"])
        self.assertFalse(a["target"]["flags"]["attempted"])
        self.assertTrue(b["target"]["flags"]["attempted"])
        self.assertFalse(analysis_dir(a).exists())

    def test_invalid_missing_legacy_and_mismatched_reports_create_no_output(self):
        for uc in ("uc-000001", "UC1", "UC-1", "UC-000000", "UC-0000001", "UC-999999"):
            with self.assertRaises(ToolError):
                resolve(self.result, uc)
        page = self.result / "tgl.md"
        page.write_text(page.read_text().replace("0 -> 1", "changed"))
        with self.assertRaises(ToolError):
            resolve(self.result, "UC-000003")
        summary = read_json(self.result / "instance_summary.json")
        summary.pop("detail_format")
        write_json(self.result / "instance_summary.json", summary)
        with self.assertRaises(ToolError):
            resolve(self.result, "UC-000001")
        self.assertFalse(list(self.root.glob("result_analyse_*")))
        self.assertFalse(list(self.result.glob("result_analyse_*")))

    def test_four_goals_and_related_missing_are_not_guessed(self):
        points = [resolve(self.result, f"UC-{n:06d}") for n in (1, 3, 5, 7)]
        goals = {p["target"]["metric"]: describe_goal(p) for p in points}
        self.assertEqual(goals["tgl"]["direction"], "0 -> 1")
        self.assertEqual(goals["cond"]["precision"], "bin_decode_required")
        self.assertEqual(goals["branch"]["precision"], "bin_decode_required")
        query = related(points[1], signal="q[0]", metric="tgl")
        self.assertEqual(len(query["points"]), 2)
        self.assertEqual(related(points[1], signal="missing")["availability"], "missing_or_excluded_or_uncollected")
        self.assertEqual(related(points[1], instance="other.u", signal="q")["availability"], "outside_recorded_selection")

    def test_native_term_values_preserve_dashes_and_unsupported_bins(self):
        from native_worker import native_goal
        terms = [{"name": name, "value": value, "value_integer": -1} for name, value in [("rst", "0"), ("en", "1"), ("later", "-")]]
        goal = native_goal("branch", [{"branch_terms": terms}])
        self.assertEqual(goal["precision"], "exact_native_terms")
        self.assertEqual([r["binary_constraint"] for r in goal["requirements"]], [0, 1, None])
        self.assertTrue(goal["requirements"][2]["dash_preserved"])
        goal = native_goal("cond", [{"condition_terms": [{"name": "en", "value": None, "value_integer": 0}]}])
        self.assertEqual(goal["requirements"][0]["binary_constraint"], 0)
        goal = native_goal("cond", [{"condition_terms": [{"name": "en", "value": None, "value_integer": -1}]}])
        self.assertEqual(goal["precision"], "bin_decode_required")

    def test_same_uc_in_two_results_cannot_share_root_output(self):
        other = self.root / "another-result"
        shutil.copytree(self.result, other)
        a, b = resolve(self.result, "UC-000001"), resolve(other, "UC-000001")
        begin(a)
        record_query(a, "only_first_result", {"value": 1})
        self.assertEqual(analysis_dir(a), self.root / "result_analyse_UC-000001")
        self.assertEqual(analysis_dir(a), analysis_dir(b))
        evidence_before = (analysis_dir(a) / "evidence.jsonl").read_bytes()
        with self.assertRaises(ToolError):
            begin(b, new_run=True)
        with self.assertRaises(ToolError):
            record_query(b, "wrong_result", {"value": 2})
        self.assertEqual((analysis_dir(a) / "evidence.jsonl").read_bytes(), evidence_before)

    def test_root_output_is_independent_of_invocation_directory(self):
        point = resolve(self.result, "UC-000003")
        begin(point)
        command = [sys.executable, str(SKILL / "scripts/coverage_query.py"), "goal",
                   "--result", str(self.result), "--uc", "UC-000003"]
        completed = subprocess.run(command, cwd=self.result, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["operation"], "describe_coverage_goal")
        self.assertEqual(analysis_dir(point).parent, self.root)
        self.assertFalse((self.result / "result_analyse_UC-000003").exists())
        manifest = read_json(analysis_dir(point) / "manifest.json")
        self.assertEqual(manifest["output_directory"], str(analysis_dir(point)))

    def test_old_output_location_requires_migration(self):
        point = resolve(self.result, "UC-000003")
        legacy = self.result / "result_analyse_UC-000003"
        legacy.mkdir();(legacy / "history.txt").write_text("keep existing evidence")
        with self.assertRaises(ToolError):
            begin(point)
        self.assertEqual((legacy / "history.txt").read_text(), "keep existing evidence")
        self.assertFalse(analysis_dir(point).exists())

    def test_external_results_use_invoking_repository_or_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            external = Path(temp) / "coverage"
            external.mkdir()
            plain_project = Path(temp) / "plain-project"
            plain_project.mkdir()
            # Keep unrelated host repositories (for example /tmp/.git) out of this case.
            with patch("runtime.Path.exists", autospec=True) as exists:
                exists.side_effect = lambda path: path == self.root / ".git"
                os.chdir(self.result)
                self.assertEqual(project_root(external), self.root)
                exists.side_effect = lambda path: False
                os.chdir(plain_project)
                self.assertEqual(project_root(external), plain_project)

    def test_same_uc_in_separate_projects_has_separate_outputs(self):
        other_root = self.root / "second-project"
        other_root.mkdir()
        other_result, _ = fixture(other_root)
        a, b = resolve(self.result, "UC-000001"), resolve(other_result, "UC-000001")
        begin(a);begin(b)
        self.assertNotEqual(analysis_dir(a), analysis_dir(b))
        self.assertEqual(analysis_dir(b).parent, other_root)

    def test_archives_identity_and_report_publication(self):
        point = resolve(self.result, "UC-000003")
        original = (self.result / "coverage_facts.sqlite").read_bytes()
        started = begin(point)
        with self.assertRaises(ToolError):
            begin(point)
        evidence = record_query(point, "describe_coverage_goal", describe_goal(point))
        draft = {"uc_id": point["uc_id"], "status": "partial", "design_match": "unconfirmed",
                 "conditions": [{"kind": "necessary", "text": "需要目标方向翻转", "evidence_ids": [evidence["evidence_id"]]}],
                 "conclusion": {"level": "候选原因", "text": "缺少历史设计对应", "evidence_ids": ["E0001"]},
                 "unresolved": ["驱动历史"], "stop_reason": "没有设计证据", "next_steps": ["核对构建记录"]}
        draft_path = analysis_dir(point) / "draft.json"
        write_json(draft_path, draft)
        finalize(point, draft_path)
        manifest = read_json(analysis_dir(point) / "manifest.json")
        self.assertEqual(manifest["status"], "partial")
        for name in ("analysis.md", "analysis.json", "manifest.json", "evidence.jsonl"):
            self.assertTrue((analysis_dir(point) / name).is_file())
        begin(point)
        self.assertTrue((analysis_dir(point) / "runs" / started["run_id"] / "analysis.md").is_file())
        self.assertEqual((self.result / "coverage_facts.sqlite").read_bytes(), original)
        changed = {**point, "target": {**point["target"], "database_id": "other"}}
        with self.assertRaises(ToolError):
            begin(changed, True)

    def test_unknown_evidence_and_unverified_root_cause_are_rejected(self):
        point = resolve(self.result, "UC-000001");begin(point)
        path = analysis_dir(point) / "draft.json"
        draft = {"uc_id": point["uc_id"], "conditions": [], "conclusion": {"level": "原因已确认", "text": "不成立的归因", "evidence_ids": ["E0001"]},
                 "unresolved": [], "next_steps": [], "stop_reason": "done"}
        write_json(path, draft)
        with self.assertRaises(ToolError):
            finalize(point, path)
        draft["conclusion"].update(level="候选原因", evidence_ids=["E9999"])
        write_json(path, draft)
        with self.assertRaises(ToolError):
            finalize(point, path)

    def test_concise_report_and_render_only_preserve_audit_data(self):
        point = resolve(self.result, "UC-000003");begin(point)
        directory = analysis_dir(point)
        failure = record_query(point, "native_coverage_semantics", {"error": "license unavailable"}, "failed")
        draft = {"uc_id": point["uc_id"], "status": "partial", "design_match": "unconfirmed",
                 "conditions": [{"kind": "necessary", "text": "q[0] 必须从 0 变为 1。", "evidence_ids": ["E0001"]}],
                 "findings": [{"text": "所选测试中该方向未覆盖。", "evidence_ids": ["E0001"]}],
                 "conclusion": {"level": "候选原因", "text": "可能缺少输入变化。", "evidence_ids": ["E0001"]},
                 "unresolved": ["缺少完整驱动历史", "许可证失败日志需保留"], "key_limitations": ["历史驱动待核对。"],
                 "stop_reason": "缺少运行证据", "next_steps": ["检查对应版本的输入驱动。"]}
        write_json(directory / "draft.json", draft)
        finalize(point, directory / "draft.json")
        saved = read_json(directory / "analysis.json")
        self.assertEqual(saved["unresolved"], draft["unresolved"])
        self.assertEqual(saved["target"]["native_locator"], point["target"]["native_locator"])
        markdown = (directory / "analysis.md").read_text()
        self.assertIn("q[0]", markdown)
        self.assertIn("0→1", markdown)
        self.assertIn("候选原因", markdown)
        self.assertIn(draft["findings"][0]["text"], markdown)
        self.assertIn(draft["next_steps"][0], markdown)
        self.assertNotIn(point["target"]["target_id"], markdown)
        self.assertNotIn("license unavailable", markdown)
        analysis_before = (directory / "analysis.json").read_bytes()
        evidence_before = (directory / "evidence.jsonl").read_bytes()
        manifest_before = read_json(directory / "manifest.json")
        render_existing(point)
        self.assertEqual((directory / "analysis.json").read_bytes(), analysis_before)
        self.assertEqual((directory / "evidence.jsonl").read_bytes(), evidence_before)
        self.assertEqual(read_json(directory / "manifest.json")["run_id"], manifest_before["run_id"])
        self.assertIn(failure["evidence_id"], saved["evidence_ids"])

    def test_render_only_rejects_target_mismatch_and_conclusion_upgrade(self):
        point = resolve(self.result, "UC-000001");begin(point)
        directory = analysis_dir(point)
        draft = {"uc_id": point["uc_id"], "conditions": [], "status": "partial",
                 "conclusion": {"level": "候选原因", "text": "需核对驱动。", "evidence_ids": ["E0001"]},
                 "unresolved": ["驱动未知"], "stop_reason": "缺少证据", "next_steps": []}
        path = directory / "draft.json";write_json(path, draft);finalize(point, path)
        analysis = read_json(directory / "analysis.json")
        original_report = (directory / "analysis.md").read_bytes()
        wrong = json.loads(json.dumps(analysis));wrong["target"]["target_id"] = "other-point"
        write_json(directory / "analysis.json", wrong)
        with self.assertRaises(ToolError):
            render_existing(point)
        wrong = json.loads(json.dumps(analysis));wrong["conclusion"]["level"] = "原因已确认"
        write_json(directory / "analysis.json", wrong)
        with self.assertRaises(ToolError):
            render_existing(point)
        wrong = json.loads(json.dumps(analysis));wrong["findings"] = [{"text": "无依据的发现", "evidence_ids": ["E9999"]}]
        write_json(directory / "analysis.json", wrong)
        with self.assertRaises(ToolError):
            render_existing(point)
        self.assertEqual((directory / "analysis.md").read_bytes(), original_report)

    def test_report_displays_conflicts_failures_and_proof_scope(self):
        point = resolve(self.result, "UC-000007")
        analysis = {"conclusion": {"level": "无法可靠分析", "text": "表达式项未解码。", "evidence_ids": ["E0001"]},
                    "conditions": [], "next_steps": ["补全原生项证据。"], "key_limitations": ["原生表达式项仍未知。"],
                    "status": "failed", "design_match": "conflict", "stop_reason": "查询失败"}
        text = render_markdown(point, analysis, analysis_dir(point))
        self.assertIn("冲突", text)
        self.assertIn("失败", text)
        self.assertIn(analysis["key_limitations"][0], text)
        analysis.update(status="complete", design_match="verified", proof_scope="仅适用于 top.u 的当前参数配置。", assumptions=["复位按编译约定执行。"])
        analysis["conclusion"]["level"] = "原因已确认"
        text = render_markdown(point, analysis, analysis_dir(point))
        self.assertIn(analysis["proof_scope"], text)
        self.assertIn(analysis["assumptions"][0], text)

    def test_portability_from_unrelated_working_directory(self):
        package = self.root / "copied-skill"
        shutil.copytree(SKILL / "scripts", package / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        command = [sys.executable, str(package / "scripts/coverage_query.py"), "resolve", "--result", str(self.result), "--uc", "UC-000007"]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["target"]["metric"], "cond")

    def test_elaborated_sequential_paths_sign_extension_concat_and_case(self):
        try:
            import pyslang
        except ImportError:
            self.skipTest("pyslang unavailable")
        from slang_backend import analyze
        point = resolve(self.result, "UC-000003")
        config = self.root / "design.json"
        write_json(config, {"cwd": str(self.root), "args": ["--top", "top", str(self.source)]})
        options = {"config": str(config), "nodes": 500, "branches": 64, "instance": None, "signal": "q[0]"}
        with redirect_stdout(io.StringIO()):
            result = analyze({"point": point, "options": options})
        updates = result["statements"]
        self.assertEqual(len(updates), 2)
        self.assertEqual(len(updates[1]["guards"]), 2)
        self.assertTrue(updates[1]["nonblocking"])
        self.assertIn("PosEdge", updates[1]["timing"][0]["edge"])
        self.assertEqual(updates[1]["bit_mapping"][0]["source"]["symbol"], "top.u.d")
        output = next(p for p in result["ports"] if p["name"] == "q")
        self.assertEqual(output["bit_mapping"][0]["actual"]["symbol"], "top.q")
        self.assertEqual(correspondence(point, result)["status"], "unconfirmed")
        dependency = {**result, "instance": {"instance_path": "top", "module_name": "top"}}
        self.assertEqual(correspondence(point, dependency)["status"], "unconfirmed")
        options["signal"] = "ext[5]"
        result = analyze({"point": point, "options": options})
        extension = result["statements"][0]["bit_mapping"][0]["source"]
        self.assertEqual(extension["extension"], "sign")
        self.assertEqual(extension["source"]["bit"], 1)
        options["signal"] = "cat[4]"
        result = analyze({"point": point, "options": options})
        self.assertEqual(result["statements"][0]["bit_mapping"][0]["source"]["bit"], 0)
        options["signal"] = "c"
        result = analyze({"point": point, "options": options})
        self.assertIn("WildcardJustZ", result["statements"][0]["guards"][0]["match_kind"])
        options["instance"], options["signal"] = "top.g[1].vinst", "q[0]"
        result = analyze({"point": point, "options": options})
        self.assertEqual(result["signal"]["width"], 3)
        options["nodes"] = 2
        result = analyze({"point": point, "options": options})
        self.assertLessEqual(len(result["nodes"]), 2)
        self.assertIn("graph_node_budget", result["boundaries"])
        self.source.write_text(self.source.read_text() + "\n// changed after extraction\n")
        self.assertEqual(correspondence(point, result)["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
