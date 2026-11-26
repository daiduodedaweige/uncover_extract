#!/usr/bin/env python3
"""Check design correspondence and inspect a bounded local semantic graph."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from runtime import (ToolError, digest_file, emit, cli_error, locked, read_json,
                     record_query, require, resolve, write_json)
from native_runner import run_worker


def correspondence(point, design, native=None, build_record=None):
    target = point["target"]
    conflicts, evidence, unresolved = [], [], []
    if design.get("status") != "success":
        return {"status": "conflict" if design.get("design_conflict") else "unconfirmed",
                "conflicts": [{"kind": "missing_or_incompatible_design_object", "error": design["error"]}] if design.get("design_conflict") else [],
                "evidence": [], "unresolved": [design.get("error", "设计查询失败")], "historical_attribution_allowed": False}
    module = design.get("instance", {}).get("module_name")
    queried_instance = design.get("instance", {}).get("instance_path")
    if queried_instance != target["instance_path"]:
        evidence.append({"kind": "dependency_instance", "instance_path": queried_instance, "module_name": module, "proves_build_equivalence": False})
    elif module != target["module_name"]:
        conflicts.append({"kind": "module_name", "coverage": target["module_name"], "design": module})
    else:
        evidence.append({"kind": "same_module_and_exact_instance", "value": module, "proves_build_equivalence": False})
    mapping = target["source_mapping"]
    path = mapping.get("path")
    if path and Path(path).is_file():
        current = digest_file(path)
        evidence.append({"kind": "mapped_source_fingerprint", "path": path, "current_sha256": current,
                         "extraction_sha256": mapping.get("sha256"), "proves_build_equivalence": False})
        if mapping.get("sha256") and current != mapping["sha256"]:
            conflicts.append({"kind": "source_changed_since_extraction", "path": path})
    else:
        unresolved.append("目标源码缺失或映射歧义")
    sources = design.get("design_identity", {}).get("sources", {})
    if sources and path:
        if path not in sources:
            unresolved.append("设计编译输入中未找到原报告映射的源码路径")
        elif Path(path).is_file() and sources[path] != digest_file(path):
            conflicts.append({"kind": "compiled_source_vs_mapped_source", "path": path})
    signal = design.get("signal")
    if signal and native and queried_instance == target["instance_path"] and not design.get("query_signal_override"):
        nodes = native.get("nodes", [])
        bits = next((n.get("instrumented_bits") for n in nodes if n.get("type") == "npiCovSignal"), None)
        if bits:
            indices = [int(m[1]) for name in bits if (m := re.search(r"\[(-?[0-9]+)\]$", name))]
            span = signal.get("range")
            if indices and span:
                observed = [max(indices), min(indices)]
                if sorted(span) != sorted(observed):
                    conflicts.append({"kind": "instrumentation_vs_design_range", "coverage_range": observed, "design_range": span,
                                      "interpretation": "可能是源码版本或部分覆盖插桩差异；解释前停止历史归因"})
            elif indices and signal.get("width") != len(set(indices)):
                conflicts.append({"kind": "instrumentation_vs_design_width", "coverage_width": len(set(indices)), "design_width": signal.get("width"),
                                  "interpretation": "声明范围接口未提供端点；宽度不同仍需解释源码版本或部分插桩差异"})
    verified = False
    if build_record:
        record = read_json(build_record)
        require(record.get("schema_version") == "coverage-build-identity-v1", "不支持的构建来源记录格式")
        actual = design["design_identity"]
        checks = {"database_id": (record.get("database_id"), target["database_id"]),
                  "design_sha256": (record.get("design_sha256"), actual.get("sha256", actual.get("fingerprint")))}
        for key, (historical, current) in checks.items():
            if not historical or historical != current:
                conflicts.append({"kind": "build_record_" + key, "historical": historical, "current": current})
        recorded_sources = record.get("source_hashes", {})
        for source_path, historical in recorded_sources.items():
            if not Path(source_path).is_file() or digest_file(source_path) != historical:
                conflicts.append({"kind": "historical_source_fingerprint", "path": source_path})
        verified = bool(recorded_sources and path in recorded_sources and record.get("compile_configuration") and record.get("origin"))
        if not verified:
            unresolved.append("构建来源记录缺少目标源码指纹、编译配置或来源")
        evidence.append({"kind": "historical_build_record", "path": str(build_record), "sha256": digest_file(build_record), "origin": record.get("origin"), "checks": checks})
    else:
        unresolved.append("缺少将 VDB、设计模型、源码及编译配置绑定到同一次历史编译的可核验证据")
    state = "conflict" if conflicts else "verified" if verified and not unresolved else "unconfirmed"
    return {"status": state, "conflicts": conflicts, "evidence": evidence,
            "unresolved": unresolved, "historical_attribution_allowed": state == "verified"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("check", "inspect", "trace"))
    p.add_argument("--result", required=True)
    p.add_argument("--uc", required=True)
    p.add_argument("--backend", required=True, choices=("kdb", "pyslang"))
    p.add_argument("--kdb", type=Path)
    p.add_argument("--config", type=Path, help="显式 pyslang 编译配置 JSON，必须放在本点分析目录内")
    p.add_argument("--verdi-home")
    p.add_argument("--build-record", type=Path, help="已有历史构建来源记录；禁止为获得 verified 反向编造")
    p.add_argument("--instance", help="trace 内部依赖查询的精确实例")
    p.add_argument("--signal", help="trace 内部依赖查询的精确信号或位")
    p.add_argument("--nodes", type=int, default=300)
    p.add_argument("--branches", type=int, default=64)
    p.add_argument("--seconds", type=int, default=60)
    args = p.parse_args()
    point = None
    try:
        require(1 <= args.nodes <= 5000 and 1 <= args.branches <= 500 and 1 <= args.seconds <= 600, "预算需为正数，最大 nodes=5000、branches=500、seconds=600")
        point = resolve(args.result, args.uc, min(args.seconds, 30))
        with locked(point) as (directory, manifest):
            require(manifest["status"] == "in_progress", "请先 begin 新运行")
            require(manifest["design_backend"] in (None, args.backend), "本运行已使用另一设计后端；显式 begin --new-run 后才能切换，禁止混用")
            manifest["design_backend"] = args.backend
            write_json(directory / "manifest.json", manifest)
            if args.backend == "pyslang":
                require(args.config is not None, "pyslang 需要 --config；不会从 RTL 目录猜测编译配置")
                require(args.config.resolve().is_relative_to(directory), "将编译配置副本保存在本点分析目录中")
            else:
                require(args.kdb is not None, "kdb 后端需要 --kdb")
        options = {"kdb": str(args.kdb.resolve()) if args.kdb else None,
                   "config": str(args.config.resolve()) if args.config else None,
                   "instance": args.instance, "signal": args.signal, "nodes": args.nodes, "branches": args.branches,
                   "verdi_home": args.verdi_home}
        result = run_worker(point, args.backend, options, args.seconds)
        result["query_signal_override"] = args.signal is not None
        # Range comparison can use an earlier native semantics query from this run.
        native = None
        with locked(point) as (directory, manifest):
            for line in (directory / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["operation"] == "native_coverage_semantics" and row["data"].get("status") == "success":
                    native = row["data"]
        result["correspondence"] = correspondence(point, result, native, args.build_record)
        result["query"] = {"operation": args.command, "options": options}
        emit(record_query(point, {"check": "check_design_match", "inspect": "inspect_logic", "trace": "trace_dependencies"}[args.command], result,
                          "success" if result.get("status") == "success" else "failed"))
        return 0 if result.get("status") == "success" else 2
    except (ToolError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        if point:
            try:
                record_query(point, "design_query", {"error": str(exc)}, "failed")
            except (ToolError, OSError):
                pass
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
