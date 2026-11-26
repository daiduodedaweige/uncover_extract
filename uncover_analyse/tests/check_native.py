#!/usr/bin/env python3
"""Validate four real VCS metrics using scripts/build_fixture.py's controlled result.

Run extraction with --all-tests first. This checks helpers, not autonomous reasoning.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import (analysis_dir, begin, emit, read_json, record_query, require,
                     resolve, write_json)
from coverage_query import describe_goal
from design_query import correspondence
from native_runner import run_worker


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", type=Path, required=True)
    args = p.parse_args()
    summary = read_json(args.result / "instance_summary.json")
    instance = next(i for i in summary["instances"] if i["instance_path"] == "fixture_tb.right")
    outputs = []
    for metric in ("line", "tgl", "branch", "cond"):
        number = instance["metrics"][metric]["pages"][0]["first_point_no"]
        point = resolve(args.result, f"UC-{number:06d}")
        begin(point, new_run=True)
        record_query(point, "describe_coverage_goal", describe_goal(point))
        native = run_worker(point, "coverage", {"verdi_home": None, "limit": 40}, 60)
        require(native["status"] == "success", f"Native {metric} failed: {native}")
        require(native["state"]["covered"] == 0 and native["state"]["coverable"] == 1, "目标状态不一致")
        require(point["target"]["selected_tests"] == ["mode0", "mode1"], "合并测试身份不一致")
        record_query(point, "native_coverage_semantics", native)
        config = analysis_dir(point) / "design-config.json"
        write_json(config, {"cwd": str(Path.cwd()), "args": ["--top", "fixture_tb", point["target"]["source_mapping"]["path"]]})
        design = run_worker(point, "pyslang", {"config": str(config), "nodes": 500, "branches": 64, "instance": None, "signal": None}, 30)
        require(design["status"] == "success", f"Design {metric} failed: {design}")
        design["correspondence"] = correspondence(point, design, native)
        record_query(point, "inspect_logic", design)
        # Constant formal port binding is independent of the coverage extraction.
        if metric != "tgl":
            sel = next(p for p in design["ports"] if p["name"] == "sel")
            require(all(b["actual"].get("literal") == "2'b0" for b in sel["bit_mapping"]), "sel 常量绑定映射错误")
        terms = native["nodes"][-1].get("condition_terms" if metric == "cond" else "branch_terms", [])
        outputs.append({"uc_id": point["uc_id"], "metric": metric, "native_state": native["state"],
                        "terms": terms, "design_match": design["correspondence"]["status"], "directory": str(analysis_dir(point))})
    emit({"status": "passed", "results": outputs})


if __name__ == "__main__":
    main()
