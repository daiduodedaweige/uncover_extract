"""Stream machine data and a readable report from the disk inventory."""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

from . import SCHEMA_VERSION
from .common import write_json


def json_rows(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        stream.write('{"schema_version":' + json.dumps(SCHEMA_VERSION) + ',"records":[\n')
        first = True
        for row in rows:
            if not first:
                stream.write(",\n")
            json.dump(row, stream, ensure_ascii=False, separators=(",", ":"))
            first = False
        stream.write("\n]}\n")


def csv_rows(path, rows):
    fields = ["target_id", "metric", "source_file", "start_line", "instance_path", "module_name",
              "status", "covered", "coverable", "raw_status", "target", "mapped_path", "mapping_status"]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([*(row.get(k) for k in fields[:10]), row["target"]["description"],
                             row["source_mapping"]["path"], row["source_mapping"]["status"]])


def cell(value):
    return str(value if value is not None else "未知").replace("|", "&#124;").replace("\n", " ").replace("`", "&#96;")


def write_results(stage, inventory, manifest, per_metric=False, include_other_states=False):
    json_rows(stage / "uncovered_coverage.json", inventory.selected())
    csv_rows(stage / "uncovered_coverage.csv", inventory.selected())
    with (stage / "report.md").open("w", encoding="utf-8") as stream:
        stream.write("# 未覆盖信息\n\n")
        stream.write(f"数据库：`{cell(manifest['input']['path'])}`。测试：`{cell(', '.join(manifest['selected_tests']))}`。\n\n")
        stream.write("以下编号行是未覆盖原子目标；对照行不计入主项。完整身份、原始状态与映射信息见 JSON。\n\n")
        stream.write("| 指标 | 已覆盖 / 可计分 | 范围内未覆盖目标 | 输出主项 |\n| --- | ---: | ---: | ---: |\n")
        for metric, counts in manifest["summary"].items():
            stream.write(f"| {metric} | {counts['native_covered']}/{counts['native_coverable']} | {counts['uncovered_candidates']} | {counts['output_count']} |\n")
        stream.write("\n覆盖率采用原生计分值；限量仅影响输出主项。缺少源码不影响覆盖目标保留。\n\n")
        stream.write("| 序号 | 指标 | 原始源码 | 行号 | 实例 | 状态 | 具体目标 |\n| ---: | --- | --- | ---: | --- | --- | --- |\n")
        for number, row in enumerate(inventory.selected(), 1):
            stream.write(f"| {number} | {row['metric']} | {cell(row['source_file'])} | {cell(row['start_line'])} | `{cell(row['instance_path'])}` | 未覆盖 | {cell(row['target']['description'])} |\n")
            for peer in row.get("same_rtl_coverage", {}).get("instances", []):
                if peer["instance_path"] != row["instance_path"]:
                    stream.write(f"| | {row['metric']} | | | `{cell(peer['instance_path'])}` | {peer['status']} ({peer['covered']}/{peer['coverable']}) | |\n")
        stream.write("\n排除验证状态：`" + manifest["exclusion_validation"]["status"] + "`。未覆盖不自动代表应当排除或不可达。\n\n")
        stream.write("[JSON](uncovered_coverage.json) · [CSV](uncovered_coverage.csv) · [运行摘要](analysis_manifest.json)\n")
    if per_metric:
        for metric in manifest["summary"]:
            json_rows(stage / f"uncovered_{metric}.json", inventory.selected(metric))
            csv_rows(stage / f"uncovered_{metric}.csv", inventory.selected(metric))
    if include_other_states:
        with gzip.open(stage / "other_states.jsonl.gz", "wt", encoding="utf-8") as stream:
            for (row,) in inventory.db.execute("SELECT record FROM special"):
                stream.write(row + "\n")
    manifest["outputs"] = sorted([p.name for p in stage.iterdir() if p.is_file()] + ["analysis_manifest.json"])
    write_json(stage / "analysis_manifest.json", manifest)
