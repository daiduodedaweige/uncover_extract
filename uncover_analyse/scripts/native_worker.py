#!/usr/bin/env python3
"""Isolated NPI coverage / KDB query worker (never a reasoning agent)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import shutil
import sys

from runtime import DesignConflict, ToolError, read_json, require, write_json
from uncover_extract.common import descriptor, fingerprint


def safe(handle, method, default=None):
    if handle is None:
        return default
    try:
        return getattr(handle, method)()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return default


def cov_value(handle, test):
    try:
        return {"value": handle.value(test), "value_integer": handle.value(test, True)}
    except (RuntimeError, TypeError, ValueError):
        return {"value": None, "value_integer": None}


def native_goal(metric, nodes):
    stem = "branch" if metric == "branch" else "condition"
    if metric not in ("branch", "cond"):
        return {"precision": "native_identity_and_state", "requirements": []}
    terms = nodes[-1].get(stem + "_terms", [])
    requirements, unknown = [], []
    for index, term in enumerate(terms):
        value = term["value"]
        if value is None and term["value_integer"] in (0, 1):
            value = str(term["value_integer"])
        if value not in ("0", "1", "-"):
            unknown.append(index)
        requirements.append({"term_index": index, "expression": term["name"], "native_value": value,
            "binary_constraint": int(value) if value in ("0", "1") else None,
            "dash_preserved": value == "-"})
    return {"precision": "exact_native_terms" if terms and not unknown and not nodes[-1].get(stem + "_terms_truncated") else "bin_decode_required",
            "requirements": requirements, "unknown_term_indices": unknown,
            "evaluation_context_required": True,
            "limitations": ["原生 '-' 保持为未约束/未求值项，不强制成 0 或 1", "term 取值要求不等于完整设计时序可达证明"]}


def coverage(request, cov):
    point, options = request["point"], request["options"]
    target = point["target"]
    original = Path(target["configuration"]["vdb"])
    before = fingerprint(original)
    require(before["sha256"] == target["database_id"], "当前 VDB 与事实库身份不一致")
    copied = Path.cwd() / "input.vdb"
    shutil.copytree(original, copied)
    require(fingerprint(copied) == before, "复制期间 VDB 发生变化")
    db = cov.open(str(copied))
    require(db is not None, "无法打开 VDB 副本")
    try:
        tests = {h.name().rsplit("/", 1)[-1]: h for h in db.test_handles()}
        names = target["selected_tests"]
        require(names and set(names) <= tests.keys(), "原生测试集合缺失或不一致")
        test = tests[names[0]]
        for name in names[1:]:
            test = cov.merge_test(test, tests[name])
            require(test is not None, "原生测试合并失败")
        for exclude in target["configuration"].get("exclude_files", []):
            require(test.load_exclude_file(exclude) == 1, "原排除配置不可用")
        instance = db.handle_by_name(target["instance_path"])
        require(instance is not None and instance.full_name() == target["instance_path"], "VDB 实例不存在")
        method = {"line": "line", "tgl": "toggle", "branch": "branch", "cond": "condition"}[target["metric"]] + "_metric_handle"
        current = getattr(instance, method)()
        ancestry = [current]
        for step in target["native_locator"]:
            children = current.child_handles()
            require(step["index"] < len(children), "原生 locator 下标不存在")
            child = children[step["index"]]
            require(descriptor(child) == {k: v for k, v in step.items() if k != "index"}, "原生 locator 描述与事实库冲突")
            for other in children:
                if other != child:
                    cov.release_handle(other)
            current = child
            ancestry.append(current)
        state = {"covered": current.covered(test), "coverable": current.coverable(test), "raw_status": current.status(test)}
        require(all(state[k] == target[k] for k in state), "原生状态与事实库不一致，可能排除配置或测试范围变化")
        nodes = []
        for handle in ancestry[1:]:
            entry = descriptor(handle)
            entry.update(cov_value(handle, test))
            entry["size"] = safe(handle, "size")
            entry["is_event_condition"] = safe(handle, "is_event_condition")
            for stem in ("branch", "condition"):
                terms = safe(handle, stem + "_term_handles", [])
                entry[stem + "_terms"] = [{**descriptor(t), **cov_value(t, test)} for t in terms[:options["limit"]]]
                entry[stem + "_terms_truncated"] = len(terms) > options["limit"]
            if entry["type"] == "npiCovSignal":
                bits = handle.child_handles()
                entry["instrumented_bits"] = [b.name() for b in bits]
                for b in bits:
                    cov.release_handle(b)
            nodes.append(entry)
        return {"status": "success", "database_id": target["database_id"], "test_scope_ref": target["test_scope_ref"],
                "nodes": nodes, "state": state,
                "goal": native_goal(target["metric"], nodes),
                "semantic_rule": "Only native term names and term values are authoritative; no bin-name decoding heuristic",
                "limitations": ["原生 term 缺失或值无明确含义时停止 bin 解码", "不恢复同周期、跨测试或历史取值轨迹"]}
    finally:
        db.close()
        require(fingerprint(original) == before, "原始 VDB 在查询期间发生变化")
        shutil.rmtree(copied)  # Only this worker's disposable copy.


class LanguageGraph:
    def __init__(self, lang, limit):
        self.lang, self.limit = lang, limit
        self.nodes, self.handles, self.boundaries = [], {}, []

    def node(self, handle, depth=0):
        if handle is None:
            return None
        if handle in self.handles:
            return self.handles[handle]
        if len(self.nodes) >= self.limit or depth > 48:
            self.boundaries.append("node_or_depth_budget")
            return None
        key = f"N{len(self.nodes) + 1}"
        self.handles[handle] = key
        kind = safe(handle, "type")
        node = {"id": key, "type": kind, "name": safe(handle, "name"), "full_name": safe(handle, "full_name"),
                "source": safe(handle, "file"), "line": safe(handle, "line_no"), "size": safe(handle, "size")}
        self.nodes.append(node)
        if kind in ("npiFor", "npiWhile", "npiRepeat", "npiForever", "npiTaskCall", "npiFuncCall", "npiReturn", "npiReturnStmt", "npiDisable", "npiForce", "npiFork", "npiRelease", "npiNetArray", "npiRegArray", "npiArrayVar", "npiIndexedPartSelect"):
            node["semantic_boundary"] = True
            self.boundaries.append("unsupported_semantics:" + kind)
        for name in ("direction", "signed", "op_type", "blocking", "case_type", "always_type"):
            if ((name in ("op_type",) and kind == "npiOperation") or
                (name == "blocking" and kind in ("npiAssignment", "npiAssignStmt")) or
                (name == "case_type" and kind == "npiCase") or
                (name == "always_type" and kind == "npiAlways") or
                (name == "direction" and kind == "npiPort") or name == "signed"):
                node[name] = safe(handle, name)
        if kind == "npiConstant":
            node["value"] = str(safe(handle, "value"))
        if safe(handle, "is_expr", False):
            try:
                node["expression"] = self.lang.expr_decompile(handle)
            except (RuntimeError, TypeError):
                pass
        # Explicit semantic relationships; never recurse parent/scope cycles.
        signal = kind in ("npiNet", "npiNetBit", "npiReg", "npiRegBit", "npiLogicVar", "npiLogicNet", "npiPort", "npiIntegerVar")
        single = ("left_range", "right_range") if signal else ("condition", "lhs", "rhs", "stmt", "else_stmt", "expr", "event_control", "delay_control", "index", "left_range", "right_range")
        edges = {}
        for stem in single:
            child = safe(handle, stem + "_handle")
            if child is not None and child != handle:
                edges[stem] = self.node(child, depth + 1)
        if not signal:
            for stem in ("operand", "stmt", "case_item"):
                children = safe(handle, stem + "_handles", [])
                if children:
                    edges[stem + "s"] = [self.node(c, depth + 1) for c in children[:self.limit]]
                    if len(children) > self.limit:
                        self.boundaries.append("branch_budget")
        node["edges"] = edges
        return key


def kdb(request, npisys, lang):
    options, point = request["options"], request["point"]
    path = Path(options["kdb"]).resolve()
    if path.is_file():
        path = path.parent
    require(path.is_dir() and (path / "simv.kdb").exists(), "--kdb 应指向含 simv.kdb 的 dbdir 或该文件")
    before = fingerprint(path)
    require(npisys.load_design(["coverage-point-analyzer", "-dbdir", str(path)]) == 1, "KDB 设计加载失败")
    target = point["target"]
    instance_name = options.get("instance") or target["instance_path"]
    instance = lang.handle_by_name(instance_name, None)
    if instance is None:
        raise DesignConflict("KDB 中不存在精确实例；不得按模块名替代")
    graph = LanguageGraph(lang, options["nodes"])
    info = {"instance_path": instance_name, "module_name": safe(instance, "def_name"), "source": safe(instance, "def_file"),
            "is_black_box": safe(instance, "is_black_box"), "contains_protected_objects": safe(instance, "contain_protect"),
            "parameters": [{"name": safe(p, "name"), "value": str(safe(p, "value"))} for p in safe(instance, "parameter_handles", [])]}
    if info["is_black_box"] == 1 or info["contains_protected_objects"] == 1:
        graph.boundaries.append("black_box_or_protected_objects")
    signal_name = options.get("signal")
    if not signal_name and target["metric"] == "tgl":
        signal_name = next((n["name"] for n in reversed(target["native_locator"][:-1]) if n["type"] in ("npiCovSignalBit", "npiCovSignal")), None)
    drivers = []
    roots_used = []
    if signal_name:
        full = instance_name + "." + signal_name
        handle = lang.handle_by_name(full, None)
        if handle is None:
            raise DesignConflict("KDB 中目标信号/位不存在")
        root = graph.node(handle)
        import re
        base = re.sub(r"\[-?[0-9]+\]$", "", signal_name)
        declaration = lang.handle_by_name(instance_name + "." + base, None)
        signal_info = {"name": base, "width": safe(declaration, "size"), "source": safe(declaration, "file"), "line": safe(declaration, "line_no")}
        left, right = safe(declaration, "left_range_handle"), safe(declaration, "right_range_handle")
        if left is not None and right is not None:
            signal_info["range"] = [safe(left, "value"), safe(right, "value")]
        traced = lang.trace_driver2(full, False)
        for driver in traced[:options["branches"]]:
            use = driver.get_use_hdl()
            context, visited = [], set()
            current = use
            while current is not None and current not in visited and len(context) < 32:
                visited.add(current)
                context.append(graph.node(current))
                current = safe(current, "upper_stmt_handle")
            drivers.append({"source": graph.node(driver.get_src_hdl()), "scope": safe(driver.get_scope_hdl(), "full_name"),
                "use": graph.node(use), "pass_through": driver.get_is_pass_thr(),
                "signals": [graph.node(s) for s in driver.get_sig_hdl_list()[:options["branches"]]], "statement_ancestry": context})
        if len(traced) > options["branches"]:
            graph.boundaries.append("driver_branch_budget")
    else:
        root = None
        signal_info = None
        # Retain local process trees; source line match is only a candidate.
        roots = safe(instance, "process_handles", []) + safe(instance, "cont_assign_handles", [])
        line = target["start_line"]
        if line is not None:
            def location_priority(h):
                start, end = safe(h, "begin_line", -1), safe(h, "end_line", -1)
                at = safe(h, "line_no", -1)
                return (0 if at == line else 1 if start and end and start <= line <= end else 2, abs((at or -1) - line))
            roots.sort(key=location_priority)
        for h in roots[:options["branches"]]:
            roots_used.append(graph.node(h))
        if len(roots) > options["branches"]:
            graph.boundaries.append("process_branch_budget")
    require(fingerprint(path) == before, "KDB 在查询期间发生变化")
    return {"status": "success", "backend": "kdb", "design_identity": {"path": str(path), "fingerprint": before["sha256"]},
        "instance": info, "signal": signal_info, "root": root, "candidate_roots": roots_used, "drivers": drivers, "nodes": graph.nodes, "boundaries": sorted(set(graph.boundaries)),
        "limitations": ["驱动列表不等于完整控制条件；须检查语句祖先和事件", "源码行只能产生候选，不能消除同一行多 bin 的歧义", "KDB 成功加载及同名对象不证明与 VDB 历史编译一致", "NPI 返回的端口/选择对象保留原样；不支持的位映射不补全"]}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    request_path, response_path = sys.argv[1:]
    request = read_json(request_path)
    initialized = False
    try:
        if request["operation"] == "pyslang":
            from slang_backend import analyze
            write_json(response_path, analyze(request))
            return 0
        home = request["options"].get("verdi_home") or request["point"]["target"]["configuration"].get("verdi_home") or os.environ.get("VERDI_HOME")
        require(home and (Path(home) / "share/NPI/python/pynpi").is_dir(), "设置 --verdi-home 或 VERDI_HOME，需可用 NPI 许可证")
        os.environ["VERDI_HOME"] = home
        sys.path.insert(0, str(Path(home) / "share/NPI/python"))
        from pynpi import npisys
        require(npisys.init(["coverage-point-analyzer"]) == 1, "NPI 初始化/许可证失败")
        initialized = True
        if request["operation"] == "coverage":
            from pynpi import cov
            result = coverage(request, cov)
        else:
            from pynpi import lang
            result = kdb(request, npisys, lang)
        write_json(response_path, result)
        return 0
    except Exception as exc:
        write_json(response_path, {"status": "unsupported_or_failed", "error": str(exc), "type": type(exc).__name__, "design_conflict": isinstance(exc, DesignConflict)})
        return 1
    finally:
        if initialized:
            npisys.end()


if __name__ == "__main__":
    raise SystemExit(main())
