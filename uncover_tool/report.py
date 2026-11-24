"""Per-instance Markdown reports with complete, bounded detail pages."""
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import quote

from .common import METRICS, require, write_json
from .facts import FactsReader
from .output import cell, instance_directory, relative_link, source_cells, target_description

LABELS = {"line": "Line", "tgl": "Toggle", "branch": "Branch", "cond": "Condition"}
OVERVIEW = ("| 模块 | 实例 | Line 未覆盖点数 | Toggle 未覆盖点数 | Branch 未覆盖点数 | Condition 未覆盖点数 |\n"
            "| --- | --- | ---: | ---: | ---: | ---: |\n")
AVAILABILITY = {"not_selected": "未选择", "not_collected_or_unavailable": "未采集或不可用",
                "no_atomic_points": "没有参与分析的原子覆盖点"}


def count_text(item):
    return str(item["uncovered_count"]) if item["status"] == "analyzed" else "—"


def overview_row(row, instance=None, values=None):
    values = values or [count_text(row["metrics"][metric]) for metric in METRICS]
    return f"| {cell(row['module_name'])} | {instance or '`' + cell(row['instance_path']) + '`'} | " + " | ".join(values) + " |\n"


def situation(row):
    items = row["metrics"].values()
    count = sum(item["uncovered_count"] for item in items)
    if count:
        return f"存在 {count:,} 个常规未覆盖点，按四个指标分别检查。"
    if any(item["atomic_count"] for item in items):
        return "没有常规未覆盖点；特殊状态及未采集指标不能据此认定为全部覆盖。"
    return "没有参与分析的原子覆盖点，不能据此认定为全部覆盖。"


def availability(row):
    return "；".join(f"{LABELS[metric]}：{AVAILABILITY[item['status']]}" for metric, item in row["metrics"].items()
                    if item["status"] != "analyzed")


def instance_state(row, text=None):
    return ("| 实例 | 覆盖情况 |\n| --- | --- |\n"
            f"| `{cell(row['instance_path'])}` | {cell(text or situation(row))} |\n\n")


def write_report(stage, manifest, page_size=2000, page_bytes=1024 * 1024):
    require(page_size > 0 and page_bytes > 1024, "Page limits must be positive; page bytes must exceed 1024")
    stage = Path(stage)
    with_reader = FactsReader(stage / "coverage_facts.sqlite")
    try:
        return _write_report(stage, manifest, with_reader, page_size, page_bytes)
    finally:
        with_reader.close()


def _write_report(stage, manifest, reader, page_size, page_bytes):
    rows = reader.summaries()
    directories = {}
    all_pages, max_bytes, total_bytes, generated = 0, 0, 0, 0
    for row in rows:
        if not any(item["uncovered_count"] for item in row["metrics"].values()):
            continue
        relative = instance_directory(row["instance_path"])
        require(relative not in directories, f"Instance directory collision: {row['instance_path']}")
        directories[relative] = row["instance_path"]
        directory = stage / relative
        directory.mkdir(parents=True, exist_ok=True)
        row["directory"] = str(relative)
        for metric, item in row["metrics"].items():
            count = item["uncovered_count"]
            if not count:
                continue
            show_target = metric != "line"
            table = ("| 源码文件 | 行号 |" + (" 信号 / 覆盖点 / 方向 |" if show_target else "") + "\n"
                     "| --- | ---: |" + (" --- |" if show_target else "") + "\n")
            @lru_cache(128)
            def source_link(sid):
                source, mapping = reader.sources[sid]
                return source_cells(source, None, mapping, directory)[0]

            iterator = iter(reader.holes(row["instance_id"], metric))
            pending = next(iterator, None)
            consumed, page = 0, 1
            while pending is not None:
                body, size = [], 0
                # Reserve room for titles, navigation, and the current instance table.
                overhead = 2048 + 8 * len(row["instance_path"].encode())
                while pending is not None and len(body) < page_size:
                    point = pending
                    description = point["description"]
                    if show_target and description is None:
                        description = target_description(metric, json.loads(point["trail"]))
                    line = (f"| {source_link(point['source'])} | {cell(point['line'])} |"
                            + (f" {cell(description)} |" if show_target else "") + "\n")
                    length = len(line.encode("utf-8"))
                    if body and size + length + overhead > page_bytes:
                        break
                    body.append(line)
                    size += length
                    pending = next(iterator, None)
                consumed += len(body)
                name = f"{metric}-{page:04d}.md"
                navigation = []
                if page > 1:
                    navigation.append(f"[上一页]({metric}-{page-1:04d}.md)")
                if pending is not None:
                    navigation.append(f"[下一页]({metric}-{page+1:04d}.md)")
                navigation += ["[实例目录](index.md)", relative_link("主报告", stage / "report.md", directory)]
                content = (f"# {cell(row['instance_path'])} / {LABELS[metric]}\n\n模块：{cell(row['module_name'])}。\n\n"
                           f"第 {page} 页，本页 {len(body):,} 个点，本指标共 {count:,} 个未覆盖点。\n\n"
                           + " · ".join(navigation) + "\n\n"
                           + instance_state(row, "以下覆盖点均未覆盖") + table + "".join(body) + "\n"
                           + " · ".join(navigation) + "\n")
                encoded = content.encode("utf-8")
                (directory / name).write_bytes(encoded)
                item["pages"].append({"path": str(relative / name), "records": len(body), "bytes": len(encoded)})
                max_bytes = max(max_bytes, len(encoded))
                total_bytes += len(encoded)
                all_pages += 1
                page += 1
            require(consumed == count, f"Uncovered count mismatch: {row['instance_path']}/{metric}: {consumed}/{count}")
            generated += count
        with (directory / "index.md").open("w", encoding="utf-8") as output:
            output.write(f"# {cell(row['instance_path'])}\n\n模块：{cell(row['module_name'])}。\n\n"
                         + relative_link("返回主报告", stage / "report.md", directory) + "\n\n"
                         + instance_state(row) + OVERVIEW + overview_row(row) + "\n" + availability(row) + "\n\n")
            for metric, item in row["metrics"].items():
                output.write(f"## {LABELS[metric]}\n\n")
                if not item["pages"]:
                    output.write((AVAILABILITY[item["status"]] if item["status"] != "analyzed" else "没有常规未覆盖点") + "。\n\n")
                else:
                    output.write(f"共 {item['uncovered_count']:,} 个未覆盖点。\n\n")
                    for number, page in enumerate(item["pages"], 1):
                        output.write(f"- [第 {number} 页]({Path(page['path']).name})：{page['records']:,} 个点\n")
                    output.write("\n")
        print(f"output: instance {row['instance_path']}, {generated} uncovered records written", flush=True)

    with (stage / "report.md").open("w", encoding="utf-8") as output:
        output.write("# 实例覆盖率分析\n\n每个实例只统计自身覆盖点，子实例单独列出。"
                     "四指标分别统计常规未覆盖原子点；— 表示未选择、未采集或没有可分析覆盖点，具体情况见实例小节。"
                     "排除点不参与分析；特殊状态保存在事实库和实例摘要中。\n\n"
                     "覆盖点按源码位置列出；未覆盖原因需要结合源码、配置、激励和波形检查。\n\n" + OVERVIEW)
        for row in rows:
            anchor = "instance-" + quote(row["instance_path"], safe="-_")
            row["anchor"] = anchor
            output.write(overview_row(row, f"[`{cell(row['instance_path'])}`](#{quote(anchor, safe='-_.')})"))
        output.write("\n## 实例分析\n\n")
        for row in rows:
            output.write(f"<a id=\"{row['anchor']}\"></a>\n\n### {cell(row['instance_path'])}\n\n"
                         f"模块：{cell(row['module_name'])}。\n\n" + instance_state(row))
            values = []
            for item in row["metrics"].values():
                if item["pages"]:
                    values.append(relative_link(count_text(item), stage / item["pages"][0]["path"], stage))
                else:
                    values.append(count_text(item))
            output.write(OVERVIEW + overview_row(row, values=values) + "\n" + availability(row) + "\n\n")
            if "directory" in row:
                output.write(relative_link("本实例全部明细页", stage / row["directory"] / "index.md", stage) + "\n\n")
    totals = Counter()
    for row in rows:
        for metric, item in row["metrics"].items():
            totals[metric] += item["uncovered_count"]
    stats = {"instances": len(rows), "instances_with_holes": len(directories), "uncovered_by_metric": dict(totals),
             "uncovered_records": sum(totals.values()), "detail_pages": all_pages, "max_detail_page_bytes": max_bytes,
             "detail_bytes": total_bytes, "page_rows_limit": page_size, "page_bytes_limit": page_bytes}
    write_json(stage / "instance_summary.json", {"schema_version": "1.0", "count_unit": "instance_atomic_point",
               "statistics": stats, "instances": rows})
    return stats
