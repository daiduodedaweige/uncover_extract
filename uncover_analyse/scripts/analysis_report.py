#!/usr/bin/env python3
"""Validate Codex's evidence-linked conclusion and publish the point report."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from runtime import (LEVELS, VERSION, ToolError, append_evidence, cli_error, emit,
                     identity, locked, read_json, require, resolve, utc, write_json)


def validate_findings(analysis, evidence_ids):
    findings = analysis.get("findings", [])
    require(isinstance(findings, list), "findings 必须为数组")
    for finding in findings:
        require(isinstance(finding, dict) and isinstance(finding.get("text"), str) and finding["text"].strip(),
                "关键发现须包含非空 text")
        refs = finding.get("evidence_ids")
        require(isinstance(refs, list) and refs and all(isinstance(ref, str) and ref in evidence_ids for ref in refs),
                "关键发现必须关联本运行已有的 evidence_ids")
    if "key_limitations" in analysis:
        require(isinstance(analysis["key_limitations"], list) and
                all(isinstance(text, str) and text.strip() for text in analysis["key_limitations"]),
                "key_limitations 必须为非空字符串组成的数组")


def evidence_link(item):
    refs = "、".join(dict.fromkeys(item["evidence_ids"]))
    return f"[{refs}](evidence.jsonl)"


def design_match(evidence):
    matches, latest = [], {}
    for entry in evidence:
        data = entry["data"]
        if "correspondence" not in data:
            continue
        state = data["correspondence"]["status"]
        matches.append(state)
        design_id = data.get("design_identity", {})
        key = design_id.get("sha256", design_id.get("fingerprint"))
        if key and data.get("status") == "success":
            latest[key] = state
    return "conflict" if "conflict" in matches else "verified" if latest and all(m == "verified" for m in latest.values()) else "unconfirmed"


def check_conclusion(point, analysis, match, evidence):
    conclusion = analysis["conclusion"]
    require(conclusion.get("level") in LEVELS and isinstance(conclusion.get("text"), str) and conclusion["text"].strip(),
            "结论须包含规范 level 与非空 text")
    require(analysis.get("design_match", match) == match, "分析设计对应状态与查询证据不一致")
    if conclusion["level"] == "原因已确认":
        require(match == "verified", "只有经过构建证据验证的设计可确认历史根因；当前只能给候选分析")
        require(analysis.get("proof_scope") and analysis.get("assumptions") is not None, "原因确认需明确 proof_scope 和 assumptions")
        require(any(e["operation"] in ("inspect_logic", "trace_dependencies", "check_design_match") and e["status"] == "success" for e in evidence), "缺少设计逻辑证据")
        if point["target"]["metric"] in ("branch", "cond"):
            require(any(e["operation"] == "native_coverage_semantics" and e["data"].get("goal", {}).get("precision") == "exact_native_terms" for e in evidence), "原生 bin 尚未解码，不能确认根因")
    if match == "conflict":
        require(conclusion["level"] in ("无法可靠分析", "候选原因"), "设计存在冲突时停止历史归因，不得声称已定位历史缺失条件")


def render_markdown(point, analysis, directory):
    """Present the decision, while full locators, flags and query history stay in JSON."""
    target, conclusion = point["target"], analysis["conclusion"]
    metric = {"tgl": "Toggle", "line": "Line", "branch": "Branch", "cond": "Condition"}[target["metric"]]
    title = f"{metric} 未覆盖"
    trail = target["native_locator"]
    if target["metric"] == "tgl" and trail:
        signal = next((node["name"] for node in reversed(trail[:-1])
                       if node["type"] in ("npiCovSignalBit", "npiCovSignal")), None)
        if signal:
            title = f"`{signal}` 的 {trail[-1]['name'].replace(' -> ', '→')} 翻转未覆盖"
    elif target.get("source_file") and target.get("start_line") is not None:
        title += f"（{Path(target['source_file']).name}:{target['start_line']}）"
    report_link = quote(os.path.relpath(point["report"]["path"], directory), safe="/")
    lines = [f"# {point['uc_id']}：{title}", "",
             f"实例：`{target['instance_path']}`；指标：{metric}；结论：**{conclusion['level']}**。", "",
             f"所属结果：`{Path(point['result']).name}` · [原报告]({report_link})", "",
             "## 覆盖需要什么条件？", ""]
    lines += [f"- {item['text']} {evidence_link(item)}" for item in analysis.get("conditions", [])] or ["目前证据不足，尚无法可靠解释覆盖条件。"]
    if analysis.get("findings"):
        lines += ["", "## 目前发现什么？", ""]
        lines += [f"- {item['text']} {evidence_link(item)}" for item in analysis["findings"]]
    lines += ["", "## 原因判断", "", f"{conclusion['text']} {evidence_link(conclusion)}"]
    if conclusion["level"] == "原因已确认":
        lines += ["", f"**适用范围：**{analysis.get('proof_scope', '详见结构化结果。')}"]
        if analysis.get("assumptions"):
            lines += ["", "**适用假设：**" + "；".join(analysis["assumptions"])]
    lines += ["", "## 下一步怎么做？", ""]
    lines += [f"{i}. {text}" for i, text in enumerate(analysis.get("next_steps", []), 1)] or ["暂无进一步建议。"]
    notes = []
    match = analysis.get("design_match", "unconfirmed")
    if match == "conflict":
        notes.append("设计与覆盖结果存在未解决的对应冲突，当前结论不能作为历史根因确认。")
    elif match == "unconfirmed":
        notes.append("设计及环境与覆盖库的历史编译对应尚未确认。")
    if analysis.get("status") == "failed":
        notes.append("本次分析失败，查询错误及日志见完整证据。")
    # Legacy reports retain all unresolved facts until Codex supplies an explicit summary.
    notes += analysis.get("key_limitations", analysis.get("unresolved", []))
    if analysis.get("status") in ("partial", "failed") and not notes:
        notes.append(analysis["stop_reason"])
    if notes:
        label = "需要先解决" if match == "conflict" else "尚未确认" if match == "unconfirmed" else "分析边界"
        lines += ["", f"**{label}：**" + " ".join(dict.fromkeys(notes))]
    lines += ["", "[完整证据](evidence.jsonl) · [结构化结果](analysis.json) · [运行信息](manifest.json)", ""]
    return "\n".join(lines)


def write_markdown(point, analysis, directory, manifest):
    report = directory / "analysis.md"
    temp = directory / "analysis.md.tmp"
    temp.write_text(render_markdown(point, analysis, directory), encoding="utf-8")
    temp.replace(report)
    manifest["report_presentation"] = {"format": "concise_v1", "renderer_version": VERSION, "rendered_utc": utc()}


def render_existing(point):
    """Refresh presentation without starting a run, querying NPI or changing evidence."""
    with locked(point) as (directory, manifest):
        analysis = read_json(directory / "analysis.json")
        require(manifest["status"] in ("complete", "partial", "failed") and analysis["status"] == manifest["status"],
                "只更新格式需要一份已结束且状态一致的分析")
        require(analysis["uc_id"] == point["uc_id"] and identity(analysis) == identity(point),
                "已保存的分析目标与报告编号身份不一致")
        require(Path(analysis["result"]).resolve() == Path(point["result"]), "已保存分析的所属结果不一致")
        evidence = [json.loads(line) for line in (directory / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
        require(all(e["identity"] == manifest["identity"] for e in evidence), "证据中存在另一目标身份")
        ids = {e["evidence_id"] for e in evidence}
        validate_findings(analysis, ids)
        check_conclusion(point, analysis, design_match(evidence), evidence)
        for item in [analysis["conclusion"], *analysis.get("conditions", [])]:
            require(item.get("evidence_ids") and set(item["evidence_ids"]) <= ids, "已保存分析引用了不存在的证据")
        write_markdown(point, analysis, directory, manifest)
        manifest.update(project_root=str(directory.parent), output_directory=str(directory))
        write_json(directory / "manifest.json", manifest)
    return {"status": analysis["status"], "report": str(directory / "analysis.md"), "operation": "render_only"}


def finalize(point, draft_path):
    with locked(point) as (directory, manifest):
        require(manifest["status"] == "in_progress", "当前运行已结束，请 begin 新运行")
        require(draft_path.resolve().is_relative_to(directory), "分析草稿须保存在本点分析目录内")
        draft = read_json(draft_path)
        require(draft.get("uc_id") == point["uc_id"], "草稿编号与目标不一致")
        evidence = [json.loads(line) for line in (directory / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
        backends = {e["data"]["backend"] for e in evidence if e["data"].get("backend")}
        require(len(backends) <= 1, "运行证据混用了不同设计后端")
        if backends:
            manifest["design_backend"] = next(iter(backends))
        ids = {e["evidence_id"] for e in evidence}
        validate_findings(draft, ids)
        conclusion = draft.get("conclusion", {})
        require(conclusion.get("level") in LEVELS and isinstance(conclusion.get("text"), str) and conclusion["text"].strip(), "结论须包含规范 level 与非空 text")
        def check_refs(item):
            refs = item.get("evidence_ids")
            require(isinstance(refs, list) and refs and all(isinstance(ref, str) and ref in ids for ref in refs), "每条结论/条件必须关联本运行已有的 evidence_ids")
        check_refs(conclusion)
        conditions = draft.get("conditions", [])
        require(isinstance(conditions, list), "conditions 必须为数组")
        for condition in conditions:
            require(condition.get("kind") in ("exact", "necessary", "candidate") and isinstance(condition.get("text"), str), "条件须含 kind=exact/necessary/candidate 与 text")
            check_refs(condition)
        for field in ("unresolved", "next_steps"):
            require(isinstance(draft.get(field), list) and all(isinstance(s, str) for s in draft[field]), f"{field} 必须为字符串数组")
        require(isinstance(draft.get("stop_reason"), str) and draft["stop_reason"].strip(), "必须说明 stop_reason")
        match = design_match(evidence)
        check_conclusion(point, draft, match, evidence)
        output = {"schema_version": "1.0", "uc_id": point["uc_id"], "result": point["result"], "target": point["target"],
                  **{key: draft[key] for key in ("conclusion", "unresolved", "stop_reason", "next_steps")}, "conditions": conditions,
                  "design_match": match, "evidence_ids": sorted(ids), "status": draft.get("status", "complete"),
                  "proof_scope": draft.get("proof_scope"), "assumptions": draft.get("assumptions", []),
                  "budget_usage": {"queries": len(evidence), "created_utc": manifest["created_utc"], "completed_utc": utc()}}
        for field in ("findings", "key_limitations"):
            if field in draft:
                output[field] = draft[field]
        require(output["status"] in ("complete", "partial", "failed"), "status 为 complete、partial 或 failed；预算耗尽应为 partial")
        write_json(directory / "analysis.json", output)
        write_markdown(point, output, directory, manifest)
        append_evidence(directory, manifest, "publish_analysis", {"status": output["status"], "level": conclusion["level"]})
        manifest["status"], manifest["completed_utc"] = output["status"], utc()
        manifest["design_match"] = match
        manifest["budget_usage"] = output["budget_usage"]
        manifest["artifacts"] = sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file() and not p.is_relative_to(directory / "runs") and p.name != ".lock")
        write_json(directory / "manifest.json", manifest)
    return {"status": output["status"], "directory": str(directory), "report": str(directory / "analysis.md"), "level": conclusion["level"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", required=True)
    p.add_argument("--uc", required=True)
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--draft", type=Path)
    action.add_argument("--render-only", action="store_true", help="仅从现有 analysis.json 更新 Markdown 展示，不重新分析")
    args = p.parse_args()
    try:
        point = resolve(args.result, args.uc)
        result = render_existing(point) if args.render_only else finalize(point, args.draft)
        return (emit(result) or 0)
    except (ToolError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
