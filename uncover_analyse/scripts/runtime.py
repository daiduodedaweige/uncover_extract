"""Identity, bounded queries and per-point evidence shared by the skill helpers."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
from uncover_extract.common import (ToolError, digest_file, require, stable_id,
                                  state_from_facts)
from uncover_extract.facts import FactsReader, point_label
from uncover_extract.output import cell, source_cells, target_description

VERSION = "1.0.2"
LEVELS = ("原因已确认", "已定位缺失条件", "候选原因", "无法可靠分析")
RECORD = re.compile(r"^- \[[ xX]\] 编号：(UC-[0-9]{6,})；源码文件：(.*?)；行号：([^；\n]+)(?:；信号 / 覆盖点 / 方向：(.*))?$", re.M)


class DesignConflict(ToolError):
    """An exact coverage object contradicts a successfully loaded design."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def validate_id(uc_id):
    require(re.fullmatch(r"UC-[0-9]{6,}", uc_id), "编号格式必须为大写 UC- 加至少六位数字，例如 UC-000123")
    number = int(uc_id[3:])
    require(number > 0 and point_label(number) == uc_id, "请按报告原样填写编号，不接受 UC-000000 或额外补零")
    return number


def result_dir(path):
    path = Path(path).expanduser().resolve()
    if path.is_file():
        # A report page scopes a result, but never substitutes for the UC id.
        candidates = [p for p in (path.parent, *path.parents) if (p / "instance_summary.json").is_file()]
        require(candidates, f"报告不属于可识别结果目录：{path}")
        path = candidates[0]
    require((path / "instance_summary.json").is_file(), f"缺少 instance_summary.json：{path}")
    require((path / "coverage_facts.sqlite").is_file(), f"缺少 coverage_facts.sqlite：{path}")
    return path


def project_root(result):
    """Prefer the result's repository, then the invocation's repository or cwd."""
    result = Path(result).resolve()
    for candidate in (result, *result.parents):
        if (candidate / ".git").exists():
            return candidate
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            return candidate
    return cwd


@contextmanager
def reader_for(result, seconds=20):
    reader = FactsReader(result / "coverage_facts.sqlite")
    deadline = time.monotonic() + seconds
    reader.db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    try:
        require(reader.version == "2.0", "旧版事实库需用 uncover_extract.rebuild_report 重建为带编号报告")
        yield reader
    finally:
        reader.close()


def point_object(reader, iid, tid):
    row = reader.db.execute(
        "SELECT i.path,i.module_name,m.metric,m.info,t.source,t.line,t.trail,s.covered,s.coverable,s.raw "
        "FROM states s JOIN instances i ON i.id=s.instance JOIN targets t ON t.id=s.target "
        "JOIN models m ON m.id=t.model WHERE s.instance=? AND s.target=?", (iid, tid)).fetchone()
    require(row is not None, "报告原子点在事实库中不存在")
    path, module, metric, info, sid, line, trail, covered, coverable, raw = row
    trail, info = json.loads(trail), json.loads(info)
    source, mapping = reader.sources[sid]
    identity = {"database_id": reader.metadata["database_id"], "test_scope_ref": reader.metadata["test_scope_ref"],
                "instance_path": path, "metric": metric, "native_locator": trail}
    return {**identity, "target_id": stable_id(list(identity.values())), "internal_target_id": tid,
            "instance_id": iid, "module_name": module or info.get("module_name"), "model": info,
            "source_file": source, "source_mapping": mapping, "start_line": line,
            "selected_tests": reader.metadata.get("selected_tests"),
            "configuration": reader.metadata["configuration"],
            **state_from_facts(int(covered), int(coverable), int(raw))}


def resolve(result, uc_id, seconds=20):
    number = validate_id(uc_id)
    result = result_dir(result)
    summary = read_json(result / "instance_summary.json")
    require(summary.get("detail_format") == "numbered_checklist_v1", "旧报告没有规范编号，请先重建报告；不得临时猜测编号")
    candidates, expected = [], 1
    # Only metadata is scanned across the design. Atomic queries use one scope.
    for instance in summary["instances"]:
        for metric in ("line", "tgl", "branch", "cond"):
            item = instance["metrics"][metric]
            first = expected
            for page in item["pages"]:
                lo, hi = page.get("first_point_no"), page.get("last_point_no")
                require(isinstance(lo, int) and isinstance(hi, int), "报告缺少页编号范围，请先重建")
                require(lo == expected and hi - lo + 1 == page["records"] and hi >= lo, "摘要页范围不连续或记录数不一致")
                if lo <= number <= hi:
                    candidates.append((instance, metric, item, page, first))
                expected = hi + 1
            require(expected - first == item["uncovered_count"], "摘要未覆盖数量与分页范围不一致")
    require(len(candidates) == 1, f"编号 {uc_id} 不存在或范围不唯一；当前结果：{result}")
    instance, metric, item, page, metric_first = candidates[0]
    report = (result / page["path"]).resolve()
    require(report.is_relative_to(result), "报告分页路径超出所属结果")
    content = report.read_text(encoding="utf-8")
    records = list(RECORD.finditer(content))
    require(len(records) == page["records"], "报告记录格式或数量与摘要不一致")
    labels = [match[1] for match in records]
    require(labels == [point_label(n) for n in range(page["first_point_no"], page["last_point_no"] + 1)], "报告编号与分页范围不一致")
    with reader_for(result, seconds) as reader:
        iid = instance["instance_id"]
        require(reader.instances.get(iid) == instance["instance_path"], "报告实例与事实库不一致")
        native_manifest = result / "analysis_manifest.json"
        if native_manifest.exists():
            manifest = read_json(native_manifest)
            require(manifest.get("input", {}).get("fingerprint", {}).get("sha256") == reader.metadata["database_id"], "原报告 manifest 与事实库 VDB 身份不一致")
            require(manifest.get("test_scope_ref") == reader.metadata["test_scope_ref"], "原报告测试集合与事实库不一致")
        count = reader.db.execute("SELECT COUNT(*) FROM states s JOIN targets t ON t.id=s.target "
            "JOIN models m ON m.id=t.model WHERE s.instance=? AND s.status='uncovered' AND m.metric=?", (iid, metric)).fetchone()[0]
        require(count == item["uncovered_count"], "报告未覆盖数量与事实库不一致")
        offset = page["first_point_no"] - metric_first
        rows = reader.db.execute("SELECT t.id,t.source,t.line,t.trail,t.description FROM states s "
            "JOIN targets t ON t.id=s.target JOIN models m ON m.id=t.model JOIN sources src ON src.id=t.source "
            "WHERE s.instance=? AND s.status='uncovered' AND m.metric=? "
            "ORDER BY src.path,t.line,t.sort_key,t.id LIMIT ? OFFSET ?", (iid, metric, len(records), offset)).fetchall()
        require(len(rows) == len(records), "报告分页与事实库记录数不一致")
        for match, (tid, sid, line, trail, description) in zip(records, rows):
            src, mapping = reader.sources[sid]
            displayed_source = source_cells(src, None, mapping, report.parent)[0]
            require((match[2], match[3]) == (displayed_source, cell(line)), f"{match[1]} 的源码位置与事实库不一致")
            expected_description = cell(description if description is not None else target_description(metric, json.loads(trail)))
            require((match[4] is None if metric == "line" else match[4] == expected_description), f"{match[1]} 的原生目标描述与事实库不一致")
        tid = rows[number - page["first_point_no"]][0]
        target = point_object(reader, iid, tid)
        require(target["module_name"] == instance["module_name"], "报告模块与事实库不一致")
        require(target["status"] == "uncovered", "所选记录不是常规未覆盖点")
    selected = records[number - page["first_point_no"]]
    return {"schema_version": "1.0", "uc_id": uc_id, "result": str(result),
            "project_root": str(project_root(result)), "target": target,
            "report": {"path": str(report), "line": content[:selected.start()].count("\n") + 1,
                       "record": selected[0], "page_range": [page["first_point_no"], page["last_point_no"]]},
            "query_scope": "summary page ranges + one instance/metric bounded page; no full-design state export",
            "limitations": ["UC 编号只在所属结果中有效", "源码映射路径和当前文件指纹不证明历史编译一致"]}


def identity(point):
    return {k: point["target"][k] for k in ("database_id", "test_scope_ref", "instance_path", "metric", "native_locator", "target_id")}


def analysis_dir(point):
    root = Path(point["project_root"]) if point.get("project_root") else project_root(point["result"])
    return root / ("result_analyse_" + point["uc_id"])


def check_result_scope(manifest, point):
    require(Path(manifest["result"]).resolve() == Path(point["result"]).resolve(),
            f"根目录中的 {point['uc_id']} 分析属于另一份覆盖结果：{manifest['result']}；"
            f"当前请求为 {point['result']}。请先另行保存旧分析目录，禁止混入同名编号。")


@contextmanager
def locked(point):
    directory = analysis_dir(point)
    require(directory.is_dir() and not directory.is_symlink(), "请先运行 coverage_query.py begin 初始化该点分析")
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = read_json(directory / "manifest.json")
        check_result_scope(manifest, point)
        require(manifest["identity"] == identity(point), "同编号已对应另一原子点，禁止混入已有分析目录")
        yield directory, manifest


def append_evidence(directory, manifest, operation, data, status="success"):
    if isinstance(data, dict) and data.get("backend"):
        require(manifest["design_backend"] in (None, data["backend"]), "本运行不能混用不同设计后端")
        manifest["design_backend"] = data["backend"]
    evidence_id = f"E{manifest.get('evidence_count', 0) + 1:04d}"
    record = {"evidence_id": evidence_id, "created_utc": utc(), "operation": operation,
              "status": status, "identity": manifest["identity"], "data": data}
    with (directory / "evidence.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    manifest["evidence_count"] = manifest.get("evidence_count", 0) + 1
    if isinstance(data, dict) and data.get("design_identity"):
        manifest["design_identity"] = data["design_identity"]
    if isinstance(data, dict) and data.get("query"):
        manifest["last_design_query"] = data["query"]
    write_json(directory / "manifest.json", manifest)
    return record


def record_query(point, operation, data, status="success"):
    with locked(point) as (directory, manifest):
        require(manifest["status"] == "in_progress", "该运行已结束，请 begin 新运行后再查询")
        return append_evidence(directory, manifest, operation, data, status)


def begin(point, new_run=False):
    directory = analysis_dir(point)
    legacy = Path(point["result"]) / directory.name
    require(not legacy.exists() or legacy == directory,
            f"发现旧位置分析目录 {legacy}；请先将其迁移到 {directory}，避免重复或丢失历史运行")
    require(not directory.is_symlink(), "分析目录不能是符号链接")
    directory.mkdir(exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = directory / "manifest.json"
        if previous.exists():
            old = read_json(previous)
            check_result_scope(old, point)
            require(old["identity"] == identity(point), "同编号已对应另一原子点，禁止覆盖或混入")
            require(old["status"] != "in_progress" or new_run, "已有进行中分析；继续查询，或用 --new-run 显式保留旧运行并重新开始")
            archive = directory / "runs" / old["run_id"]
            require(not archive.exists(), "历史运行目录已存在")
            archive.mkdir(parents=True)
            for path in directory.iterdir():
                if path.name not in (".lock", "runs"):
                    shutil.move(str(path), archive / path.name)
        else:
            require(not [p for p in directory.iterdir() if p.name != ".lock"], "已有非本工具目录内容，拒绝覆盖")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        manifest = {"schema_version": "1.0", "tool": "coverage-point-analyzer", "tool_version": VERSION,
            "python": sys.version, "uc_id": point["uc_id"], "result": point["result"], "run_id": run_id,
            "project_root": str(directory.parent), "output_directory": str(directory),
            "identity": identity(point), "selected_tests": point["target"]["selected_tests"],
            "original_report": point["report"], "configuration": point["target"]["configuration"],
            "created_utc": utc(), "status": "in_progress", "design_backend": None, "evidence_count": 0,
            "budget": {"max_queries": 24, "max_seconds": 600}, "artifacts": []}
        write_json(previous, manifest)
        append_evidence(directory, manifest, "resolve_point", point)
        analysis = {"schema_version": "1.0", "uc_id": point["uc_id"], "result": point["result"],
            "target": point["target"], "conditions": [], "conclusion": {"level": "无法可靠分析", "text": "分析进行中，尚未形成结论", "evidence_ids": ["E0001"]},
            "design_match": "unconfirmed", "evidence_ids": ["E0001"], "unresolved": ["设计对应及局部逻辑尚未查询"],
            "stop_reason": "分析进行中", "next_steps": ["核对设计身份并查询原生语义"], "status": "in_progress"}
        write_json(directory / "analysis.json", analysis)
        (directory / "analysis.md").write_text(f"# {point['uc_id']}\n\n所属结果：{point['result']}\n\n实例：`{point['target']['instance_path']}`；指标：{point['target']['metric']}；结论：无法可靠分析（进行中）。\n\n[原报告]({quote(os.path.relpath(point['report']['path'], directory), safe='/')})\n\n已唯一定位原子点，等待设计与语义证据。 [E0001]\n", encoding="utf-8")
        manifest["artifacts"] = ["analysis.md", "analysis.json", "evidence.jsonl", "manifest.json"]
        write_json(previous, manifest)
    return {"directory": str(directory), "run_id": run_id, "point": point}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cli_error(exc):
    emit({"status": "error", "error": str(exc), "type": type(exc).__name__})
    return 2
