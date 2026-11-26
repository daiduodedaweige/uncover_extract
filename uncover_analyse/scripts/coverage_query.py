#!/usr/bin/env python3
"""Resolve one report UC id and query its native, lossless coverage facts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from runtime import (ToolError, begin, cli_error, emit, point_object, reader_for,
                     record_query, require, resolve)


def describe_goal(point):
    target = point["target"]
    trail, metric = target["native_locator"], target["metric"]
    goal = {"metric": metric, "native_bin": trail[-1] if trail else None,
            "native_objects": trail, "precision": "native_bin_identity",
            "scoring_configuration": "coverage facts retain statuses; historical instrumentation options require compile evidence",
            "limitations": []}
    if metric == "tgl":
        signal = next((step["name"] for step in reversed(trail[:-1]) if step["type"] in ("npiCovSignalBit", "npiCovSignal")), None)
        direction = trail[-1]["name"]
        goal.update(signal=signal, direction=direction)
        if signal and direction in ("0 -> 1", "1 -> 0"):
            goal.update(precision="exact_transition_goal", requirement=f"{signal} 发生可计分的 {direction} 翻转")
        else:
            goal["limitations"].append("未知原生 toggle 方向或缺少信号位；停止值条件推导")
        goal["limitations"].append("未覆盖不证明信号恒定；X/Z、时间窗口及门控需核对编译计分配置")
    elif metric == "line":
        goal.update(requirement="执行该完整原生 locator 所标识的 statement bin", precision="exact_native_statement_goal")
        goal["limitations"].append("同一物理行可有多个 statement bin；源码行候选不能替代原生对象对应")
    else:
        goal.update(requirement="选择该原生分支结果" if metric == "branch" else "在求值上下文中满足该原生 condition bin",
                    precision="bin_decode_required", pattern=trail[-1]["name"])
        goal["limitations"].append("需要 native 查询的原生 term 顺序及值；不可根据 bin 名称推断真假、真值表或 MC/DC")
    return goal


def related(point, instance=None, metric=None, signal=None, line=None, limit=40, seconds=20):
    require(signal is not None or line is not None, "相关查询必须指定精确信号名或源码行，禁止无范围扫描")
    require(1 <= limit <= 200, "limit 范围为 1..200")
    instance = instance or point["target"]["instance_path"]
    with reader_for(Path(point["result"]), seconds) as reader:
        row = reader.db.execute("SELECT id,selected FROM instances WHERE path=?", (instance,)).fetchone()
        if row is None or not row[1]:
            return {"instance_path": instance, "availability": "outside_recorded_selection", "points": [],
                    "limitations": ["未提取实例不能当作未覆盖"]}
        iid = row[0]
        params, where = [iid], ["s.instance=?"]
        if metric:
            where.append("m.metric=?"); params.append(metric)
        if line is not None:
            where.append("t.line=?"); params.append(line)
        if signal:
            # JSON matching avoids LIKE wildcard interpretation and name substrings.
            where.append("EXISTS (SELECT 1 FROM json_each(t.trail) j WHERE "
                "json_extract(j.value,'$.type') IN ('npiCovSignal','npiCovSignalBit') "
                "AND json_extract(j.value,'$.name')=?)")
            params.append(signal)
        query = ("SELECT t.id FROM states s JOIN targets t ON t.id=s.target JOIN models m ON m.id=t.model WHERE "
                 + " AND ".join(where) + " ORDER BY t.id LIMIT ?")
        tids = [row[0] for row in reader.db.execute(query, (*params, limit + 1))]
        points = [point_object(reader, iid, tid) for tid in tids[:limit]]
    return {"instance_path": instance, "filter": {"metric": metric, "signal": signal, "line": line},
            "availability": "recorded" if points else "missing_or_excluded_or_uncollected",
            "points": points, "truncated": len(tids) > limit,
            "limitations": ["排除点在原提取时可能已移除；无记录不能区分未采集、排除和不存在", "合并覆盖不能证明共同发生或事件先后"]}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("resolve", "begin", "goal", "related", "native"))
    p.add_argument("--result", required=True, help="结果目录或该结果中的报告路径")
    p.add_argument("--uc", required=True, help="报告原样的大写 UC-000123 编号")
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--new-run", action="store_true", help="显式归档进行中分析并开始同目标新运行")
    p.add_argument("--instance", help="内部相关查询的精确实例")
    p.add_argument("--metric", choices=("line", "tgl", "branch", "cond"))
    p.add_argument("--signal", help="精确信号名，可包含 [bit]")
    p.add_argument("--line", type=int)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--verdi-home")
    return p


def main():
    args = parser().parse_args()
    point = None
    try:
        require(1 <= args.seconds <= 600, "seconds 范围为 1..600")
        point = resolve(args.result, args.uc, args.seconds)
        if args.command == "resolve":
            emit(point)
        elif args.command == "begin":
            emit(begin(point, args.new_run))
        elif args.command == "goal":
            emit(record_query(point, "describe_coverage_goal", describe_goal(point)))
        elif args.command == "related":
            emit(record_query(point, "query_related_coverage", related(point, args.instance, args.metric, args.signal, args.line, args.limit, args.seconds)))
        else:
            from native_runner import run_worker
            require(1 <= args.limit <= 200, "limit 范围为 1..200")
            data = run_worker(point, "coverage", {"verdi_home": args.verdi_home, "limit": args.limit}, args.seconds)
            emit(record_query(point, "native_coverage_semantics", data, "success" if data.get("status") == "success" else "failed"))
            return 0 if data.get("status") == "success" else 2
        return 0
    except (ToolError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        if point and args.command not in ("resolve", "begin"):
            try:
                record_query(point, args.command, {"error": str(exc)}, "failed")
            except (ToolError, OSError):
                pass
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
