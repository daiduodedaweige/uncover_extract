"""Explicit pyslang elaboration backend; retain AST semantics and unknown boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex

from runtime import DesignConflict, digest_file, read_json, require, stable_id


class Model:
    def __init__(self, compilation, instance, options, ast):
        self.compilation, self.instance, self.options, self.a = compilation, instance, options, ast
        self.sm = compilation.sourceManager
        self.nodes, self.seen, self.boundaries = [], {}, set()
        self.visits = 0

    def location(self, obj):
        span = getattr(obj, "sourceRange", None)
        if span is None and getattr(obj, "syntax", None) is not None:
            span = obj.syntax.sourceRange
        start = span.start if span is not None else getattr(obj, "location", None)
        if start is None:
            return {}
        return {"source": str(self.sm.getFullPath(start.buffer)), "line": self.sm.getLineNumber(start),
                "column": self.sm.getColumnNumber(start),
                "end_line": self.sm.getLineNumber(span.end) if span is not None else self.sm.getLineNumber(start)}

    def add(self, obj, kind, **fields):
        if obj in self.seen:
            return self.seen[obj]
        if len(self.nodes) >= self.options["nodes"]:
            self.boundaries.add("graph_node_budget")
            return None
        key = f"N{len(self.nodes) + 1}"
        self.seen[obj] = key
        self.nodes.append({"id": key, "kind": kind, **self.location(obj), **fields})
        return key

    def constant_int(self, expression):
        try:
            value = expression.eval(self.a.EvalContext(self.instance))
            integer = value.value
            if hasattr(integer, "hasUnknown") and not integer.hasUnknown:
                return int(integer)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        return None

    def expr(self, expression, depth=0):
        if expression is None:
            return None
        if expression in self.seen:
            return self.seen[expression]
        if depth > 48:
            self.boundaries.add("expression_depth_budget")
            return None
        kind = type(expression).__name__
        syntax = getattr(expression, "syntax", None)
        text = str(syntax).strip() if syntax is not None else None
        key = self.add(expression, kind, text=text[:2000] if text else None, text_truncated=bool(text and len(text) > 2000),
                       type=str(expression.type), width=expression.type.bitWidth, signed=expression.type.isSigned)
        if key is None:
            return None
        node = self.nodes[int(key[1:]) - 1]
        edges = {}
        a = self.a
        if isinstance(expression, a.NamedValueExpression):
            node["symbol"] = expression.symbol.hierarchicalPath
            node["declared_type"] = str(expression.symbol.type)
            if isinstance(expression.symbol, a.ParameterSymbol):
                node["constant_value"] = str(expression.symbol.value)
        elif isinstance(expression, (a.IntegerLiteral, a.UnbasedUnsizedIntegerLiteral)):
            node["value"] = str(expression.value)
        elif isinstance(expression, (a.BinaryExpression, a.AssignmentExpression)):
            edges = {"left": self.expr(expression.left, depth + 1), "right": self.expr(expression.right, depth + 1)}
            node["op"] = str(expression.op)
            if isinstance(expression, a.AssignmentExpression):
                node["nonblocking"] = expression.isNonBlocking
        elif isinstance(expression, (a.UnaryExpression, a.ConversionExpression)):
            edges["operand"] = self.expr(expression.operand, depth + 1)
            node["op"] = str(getattr(expression, "op", getattr(expression, "conversionKind", "")))
        elif isinstance(expression, a.ConcatenationExpression):
            edges["operands_msb_first"] = [self.expr(x, depth + 1) for x in expression.operands]
        elif isinstance(expression, a.ElementSelectExpression):
            edges = {"value": self.expr(expression.value, depth + 1), "selector": self.expr(expression.selector, depth + 1)}
            node["constant_index"] = self.constant_int(expression.selector)
            if node["constant_index"] is None:
                self.boundaries.add("dynamic_index")
        elif isinstance(expression, a.RangeSelectExpression):
            edges = {k: self.expr(getattr(expression, k), depth + 1) for k in ("value", "left", "right")}
            node.update(selection_kind=str(expression.selectionKind), left=self.constant_int(expression.left), right=self.constant_int(expression.right))
        elif isinstance(expression, a.ConditionalExpression):
            edges = {"conditions": [self.expr(c.expr, depth + 1) for c in expression.conditions],
                     "if_true": self.expr(expression.left, depth + 1), "if_false": self.expr(expression.right, depth + 1)}
        else:
            node["unsupported"] = True
            self.boundaries.add("unsupported_expression:" + kind)
        node["edges"] = edges
        return key

    def bit(self, expression, offset, depth=0):
        """Map one packed result bit (LSB offset) without flattening arithmetic."""
        a = self.a
        if depth > 32:
            return {"unknown": "bit_mapping_depth_budget"}
        if not expression.type.isIntegral or offset < 0 or offset >= expression.type.bitWidth:
            return {"unknown": "out_of_range_or_non_integral"}
        recurse = lambda e, b: self.bit(e, b, depth + 1)
        if isinstance(expression, a.NamedValueExpression):
            if isinstance(expression.symbol, a.ParameterSymbol):
                return {"literal": str(expression.symbol.value), "bit_offset": offset, "parameter": expression.symbol.hierarchicalPath}
            if not expression.type.isSimpleBitVector:
                return {"unknown": "aggregate_bit_layout", "expression": self.expr(expression)}
            span = expression.type.getBitVectorRange()
            index = span.right + offset if span.isDescending else span.right - offset
            return {"symbol": expression.symbol.hierarchicalPath, "bit": index, "offset": offset}
        if isinstance(expression, a.ConversionExpression):
            operand = expression.operand
            if not operand.type.isIntegral:
                return {"unknown": "non_integral_conversion", "expression": self.expr(expression)}
            if operand.type.isFourState and not expression.type.isFourState:
                return {"conversion": "four_state_to_two_state", "expression": self.expr(expression), "bit_offset": offset}
            if offset < operand.type.bitWidth:
                return recurse(operand, offset)
            if operand.type.isSigned:
                return {"extension": "sign", "source": recurse(operand, operand.type.bitWidth - 1)}
            return {"constant": "0", "extension": "zero"}
        if isinstance(expression, a.ConcatenationExpression):
            for operand in reversed(list(expression.operands)):
                if offset < operand.type.bitWidth:
                    return recurse(operand, offset)
                offset -= operand.type.bitWidth
        if isinstance(expression, a.ElementSelectExpression) and expression.value.type.isSimpleBitVector:
            index = self.constant_int(expression.selector)
            if index is not None:
                span = expression.value.type.getBitVectorRange()
                if span.containsPoint(index):
                    return recurse(expression.value, span.translateIndex(index))
            return {"unknown": "dynamic_or_out_of_range_select", "expression": self.expr(expression)}
        if isinstance(expression, a.RangeSelectExpression) and expression.value.type.isSimpleBitVector:
            left, right = self.constant_int(expression.left), self.constant_int(expression.right)
            if left is not None and right is not None:
                span = expression.value.type.getBitVectorRange()
                kind = str(expression.selectionKind).split(".")[-1]
                if kind == "Simple":
                    index = right + offset if left >= right else right - offset
                elif kind == "IndexedUp":
                    index = left + offset if span.isDescending else left + right - 1 - offset
                elif kind == "IndexedDown":
                    index = left - right + 1 + offset if span.isDescending else left - offset
                else:
                    return {"unknown": "range_selection_kind"}
                if span.containsPoint(index):
                    return recurse(expression.value, span.translateIndex(index))
            return {"unknown": "dynamic_or_out_of_range_slice", "expression": self.expr(expression)}
        if isinstance(expression, (a.IntegerLiteral, a.UnbasedUnsizedIntegerLiteral)):
            # Preserve X/Z: textual native literal, never coerce unknown bits to 0.
            return {"literal": str(expression.value), "bit_offset": offset}
        if isinstance(expression, a.ConditionalExpression):
            return {"select": [self.expr(c.expr) for c in expression.conditions],
                    "if_true": recurse(expression.left, offset), "if_false": recurse(expression.right, offset)}
        return {"expression": self.expr(expression), "bit_offset": offset, "mapping": "expression_dependency"}

    def timing(self, timing):
        result = {"kind": type(timing).__name__, **self.location(timing)}
        if hasattr(timing, "expr"):
            result.update(expression=self.expr(timing.expr), edge=str(getattr(timing, "edge", "")))
        if getattr(timing, "iffCondition", None) is not None:
            result["iff"] = self.expr(timing.iffCondition)
        if hasattr(timing, "events"):
            result["events"] = [self.timing(e) for e in timing.events]
        return result

    def walk(self, stmt, guards=(), timing=(), order=(), process=None, depth=0):
        if stmt is None:
            return []
        self.visits += 1
        if self.visits > 10000 or depth > 64:
            self.boundaries.add("statement_visit_budget")
            return []
        a = self.a
        if isinstance(stmt, a.BlockStatement):
            return self.walk(stmt.body, guards, timing, order, process, depth + 1)
        if isinstance(stmt, a.StatementList):
            rows = []
            for i, child in enumerate(stmt.list):
                rows += self.walk(child, guards, timing, (*order, i), process, depth + 1)
            return rows
        if isinstance(stmt, a.TimedStatement):
            return self.walk(stmt.stmt, guards, (*timing, self.timing(stmt.timing)), order, process, depth + 1)
        if isinstance(stmt, a.ConditionalStatement):
            conditions = [self.expr(c.expr) for c in stmt.conditions]
            if any(c.pattern is not None for c in stmt.conditions):
                self.boundaries.add("conditional_pattern")
            row = {"kind": "decision", **self.location(stmt), "conditions": conditions, "guards": list(guards), "timing": list(timing), "order": list(order), "process": process}
            return [row] + self.walk(stmt.ifTrue, (*guards, {"conditions": conditions, "outcome": "true"}), timing, (*order, 0), process, depth + 1) + self.walk(stmt.ifFalse, (*guards, {"conditions": conditions, "outcome": "false_or_unknown"}), timing, (*order, 1), process, depth + 1)
        if isinstance(stmt, a.CaseStatement):
            selector, prior, rows = self.expr(stmt.expr), [], []
            kind = str(stmt.condition)
            rows.append({"kind": "case_decision", **self.location(stmt), "selector": selector, "match_kind": kind, "guards": list(guards), "timing": list(timing), "order": list(order), "process": process})
            for i, item in enumerate(stmt.items):
                patterns = [self.expr(e) for e in item.expressions]
                guard = {"selector": selector, "patterns": patterns, "prior_items_not_selected": list(prior), "match_kind": kind}
                rows += self.walk(item.stmt, (*guards, guard), timing, (*order, i), process, depth + 1)
                prior.append(patterns)
            if stmt.defaultCase is not None:
                rows += self.walk(stmt.defaultCase, (*guards, {"selector": selector, "default": True, "prior_items_not_selected": prior, "match_kind": kind}), timing, (*order, len(prior)), process, depth + 1)
            return rows
        if isinstance(stmt, a.ExpressionStatement):
            e = stmt.expr
            row = {"kind": "statement", **self.location(stmt), "guards": list(guards), "timing": list(timing), "order": list(order), "process": process,
                   "_expression": e}
            if isinstance(e, a.AssignmentExpression):
                row.update(kind="assignment", nonblocking=e.isNonBlocking, _lhs=e.left, _rhs=e.right)
            return [row]
        if isinstance(stmt, a.EmptyStatement):
            return []
        self.boundaries.add("unsupported_statement:" + type(stmt).__name__)
        return [{"kind": "unsupported_statement", "type": type(stmt).__name__, **self.location(stmt), "guards": list(guards), "timing": list(timing), "order": list(order), "process": process}]

    def references(self, expression):
        refs = set()
        def visit(node):
            if isinstance(node, self.a.NamedValueExpression):
                refs.add(node.symbol.hierarchicalPath)
        expression.visit(visit)
        return refs


def analyze(request):
    import pyslang
    from pyslang import ast as a
    require(pyslang.__version__.split('.')[0] == '11', "当前 pyslang 版本未验证；需 >=11,<12")
    options, point = request["options"], request["point"]
    config = read_json(options["config"])
    require(isinstance(config.get("args"), list) and config["args"] and all(isinstance(x, str) for x in config["args"]), "pyslang 配置必须含完整编译 args 字符串数组（filelist/宏/include/顶层/参数）")
    cwd = Path(config.get("cwd", Path(options["config"]).parent)).resolve()
    require(cwd.is_dir(), "编译配置 cwd 不存在")
    # This worker never emits compile artifacts; NPI is not loaded here.
    os.chdir(cwd)
    driver = pyslang.driver.Driver()
    driver.addStandardArgs()
    require(driver.parseCommandLine(shlex.join(["slang", *config["args"]])), "pyslang 编译参数无效")
    require(driver.processOptions() and driver.parseAllSources(), "pyslang 源码解析失败；检查后端日志")
    compilation = driver.createCompilation()
    root = compilation.getRoot()
    diagnostics = compilation.getAllDiagnostics()
    driver.reportCompilation(compilation, True)
    require(not compilation.hasIssuedErrors and not compilation.hasFatalErrors, "pyslang 编译存在错误；停止使用部分 AST 做归因")
    instance_name = options.get("instance") or point["target"]["instance_path"]
    design_name = config.get("instance_map", {}).get(instance_name, instance_name)
    instance = root.lookupName(design_name)
    if not isinstance(instance, a.InstanceSymbol):
        raise DesignConflict("展开模型中不存在精确实例；如需层级映射请显式配置 instance_map")
    model = Model(compilation, instance, options, a)
    signal = options.get("signal")
    if not signal and point["target"]["metric"] == "tgl":
        signal = next((n["name"] for n in reversed(point["target"]["native_locator"][:-1]) if n["type"] in ("npiCovSignalBit", "npiCovSignal")), None)
    bit_match = re.fullmatch(r"(.+)\[(-?[0-9]+)\]", signal or "")
    base, bit = (bit_match[1], int(bit_match[2])) if bit_match else (signal, None)
    symbol = instance.body.lookupName(base) if base else None
    if base is not None and not isinstance(symbol, a.ValueSymbol):
        raise DesignConflict("展开模型中不存在目标信号")
    if symbol is not None and bit is not None:
        if not symbol.type.isSimpleBitVector or not symbol.type.getBitVectorRange().containsPoint(bit):
            raise DesignConflict("目标位超出设计声明范围，或目标聚合类型位布局未支持")
    statements = []
    # Generate scopes are already elaborated; skip nested module bodies.
    def members(scope):
        for member in scope:
            yield member
            if isinstance(member, a.GenerateBlockSymbol):
                yield from members(member)
            elif isinstance(member, a.GenerateBlockArraySymbol):
                for block in member.entries:
                    yield from members(block)
    children = list(members(instance.body))
    for member in children:
        if isinstance(member, a.ProceduralBlockSymbol):
            statements += model.walk(member.body, process={"kind": str(member.procedureKind), **model.location(member)})
        elif isinstance(member, a.ContinuousAssignSymbol):
            e = member.assignment
            statements.append({"kind": "continuous_assignment", **model.location(e), "guards": [], "timing": [], "order": [],
                "_expression": e, "_lhs": e.left, "_rhs": e.right})
    selected, assignment_count = [], 0
    target_line = point["target"]["start_line"]
    target_source = point["target"]["source_mapping"].get("path")
    for statement in statements:
        if symbol is not None:
            keep = "_lhs" in statement and symbol.hierarchicalPath in model.references(statement["_lhs"])
        else:
            keep = target_line is not None and statement.get("line", 0) <= target_line <= statement.get("end_line", -1)
            if target_source:
                keep = keep and statement.get("source") == str(Path(target_source).resolve())
        if not keep:
            continue
        if len(selected) >= options["branches"]:
            model.boundaries.add("selected_statement_budget")
            break
        result = {k: v for k, v in statement.items() if not k.startswith("_")}
        if "_expression" in statement:
            result["expression"] = model.expr(statement["_expression"])
        if "_lhs" in statement:
            result.update(lhs=model.expr(statement["_lhs"]), rhs=model.expr(statement["_rhs"]),
                          data_dependencies=sorted(model.references(statement["_rhs"])))
            width = statement["_lhs"].type.bitWidth
            offsets = range(min(width, 32))
            if symbol is not None and bit is not None and isinstance(statement["_lhs"], a.NamedValueExpression):
                offsets = [symbol.type.getBitVectorRange().translateIndex(bit)]
            result["bit_mapping"] = [{"destination": model.bit(statement["_lhs"], offset), "source": model.bit(statement["_rhs"], offset)} for offset in offsets]
            result["bit_mapping_truncated"] = bit is None and width > 32
            assignment_count += 1
        selected.append(result)
    ports = []
    for port in instance.body.portList:
        if not isinstance(port, a.PortSymbol) or (base and port.name != base):
            continue
        connection = instance.getPortConnection(port)
        expr = connection.expression
        direction = str(port.direction)
        if direction.endswith(".Out") and isinstance(expr, a.AssignmentExpression):
            expr = expr.left  # slang represents an output actual as an assignment lvalue.
        info = {"name": port.name, "direction": str(port.direction), "type": str(port.type), "width": port.type.bitWidth,
                "connection": model.expr(expr), "scope": instance.hierarchicalPath,
                "boundary": "top_level_input" if instance.parentScope == root and str(port.direction).endswith(".In") else "instance_port"}
        if expr is not None:
            offsets = [symbol.type.getBitVectorRange().translateIndex(bit)] if bit is not None and port.name == base else range(min(port.type.bitWidth, 32))
            info["bit_mapping"] = [{"port_offset": offset, "actual": model.bit(expr, offset)} for offset in offsets]
            info["dependencies"] = sorted(model.references(expr))
        if str(port.direction).endswith((".InOut", ".Ref")):
            model.boundaries.add("inout_or_ref_port")
        ports.append(info)
    if symbol is not None:
        # Outgoing child ports can drive a signal in the current scope.
        for child in children:
            if not isinstance(child, a.InstanceSymbol):
                continue
            for port in child.body.portList:
                if not isinstance(port, a.PortSymbol) or not str(port.direction).endswith((".Out", ".InOut")):
                    continue
                expr = child.getPortConnection(port).expression
                if str(port.direction).endswith(".Out") and isinstance(expr, a.AssignmentExpression):
                    expr = expr.left
                if expr is not None and symbol.hierarchicalPath in model.references(expr):
                    ports.append({"driver_instance": child.hierarchicalPath, "port": port.name, "direction": str(port.direction),
                                  "actual_expression": model.expr(expr), "boundary": "child_output",
                                  "bit_mapping": [{"formal_offset": offset, "actual": model.bit(expr, offset)} for offset in range(min(port.type.bitWidth, 32))],
                                  "bit_mapping_truncated": port.type.bitWidth > 32})
    if assignment_count > 1:
        model.boundaries.add("multiple_assignments_preserve_process_order_and_priority")
    buffers = {}
    sm = compilation.sourceManager
    for buffer in sm.getAllBuffers():
        path = Path(str(sm.getFullPath(buffer)))
        if path.is_file():
            buffers[str(path.resolve())] = digest_file(path)
    design_identity = {"backend": "pyslang", "version": pyslang.__version__, "configuration": config, "sources": buffers}
    signal_info = None
    if symbol is not None:
        signal_info = {"name": symbol.name, "path": symbol.hierarchicalPath, "type": str(symbol.type), "width": symbol.type.bitWidth, **model.location(symbol)}
        if symbol.type.isSimpleBitVector:
            span = symbol.type.getBitVectorRange()
            signal_info["range"] = [span.left, span.right]
    return {"status": "success", "backend": "pyslang", "design_identity": {**design_identity, "sha256": stable_id(design_identity)},
        "instance": {"instance_path": instance_name, "elaborated_path": design_name, "module_name": instance.definition.name,
                     "parameters": [{"name": p.name, "value": str(p.value)} for p in instance.body.parameters]},
        "signal": signal_info, "statements": selected, "ports": ports, "nodes": model.nodes,
        "boundaries": sorted(model.boundaries), "diagnostics_count": len(diagnostics),
        "limitations": ["重建模型成功不证明与历史编译一致", "保留阻塞/非阻塞赋值和语句顺序；未做完整过程符号执行或时序可达证明", "同一行多个原生 bin 与 AST 对应仍需原生对象证据", "不支持的过程不会递归猜测，反馈与寄存器保持条件由分析者结合过程整理"]}
