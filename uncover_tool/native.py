"""Native reads and optional XML identity hints, isolated to the worker process."""
from __future__ import annotations

from collections import Counter
from bisect import insort
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET

from .common import (METRICS, ToolError, in_scope, location, native_totals,
                     require, stable_id, state, walk_bins)
from .comparison import PeerComparison


def read_xml(path):
    data = path.read_bytes()
    return ET.fromstring(gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data)


def xml_hints(vdb):
    """No hit data are decoded here. Unknown metadata disables comparison hints."""
    base = vdb / "snps/coverage/db"
    definitions, metric_models, warnings = {}, {}, []
    try:
        design = read_xml(base / "design/verilog.design.xml")
        version = design.find("ucapi_version")
        if version is None or version.get("major_ver") != "13":
            return {}, {}, ["XML identity hints unavailable for this metadata version; native reads remain enabled"]
        files = {n.get("id"): n.get("name") for n in read_xml(base / "auxiliary/verilog.sourceinfo.xml").findall("fileinfo")}
        nodes = {i.get("id"): (i, d) for d in design.findall("srcdef") for i in d.findall("srcinst")}
        names = {}

        def name(node, active=None):
            key = node.get("id")
            if key in names:
                return names[key]
            active = set() if active is None else active
            require(key not in active, "Cycle in XML instance hierarchy")
            active.add(key)
            parent = node.get("pid")
            names[key] = node.get("name") if parent == "-1" else name(nodes[parent][0], active) + "." + node.get("name")
            active.remove(key)
            return names[key]

        for node, definition in nodes.values():
            definitions[name(node)] = {"definition_id": definition.get("id"),
                "definition_checksum": definition.get("chksum"),
                "sources": sorted({files[n.get("file_id")] for n in definition.findall("srclocmult") if n.get("file_id") in files}),
                "parameters": None, "parameter_evidence": "native definition/model identity; explicit values unavailable"}
        for metric in METRICS:
            path = base / f"shape/{metric}.verilog.info.xml"
            if path.exists():
                metric_models[metric] = {i.get("name"): i.get("chksum") for i in read_xml(path).findall("instance_data")}
    except (OSError, ET.ParseError, KeyError, ToolError) as exc:
        warnings.append(f"XML identity hints unavailable: {exc}; incompatible/unknown models will not be compared")
        return {}, {}, warnings
    return definitions, metric_models, warnings


class NativeSession:
    def __init__(self, config, copied_vdb):
        self.config, self.vdb = config, copied_vdb
        self.db = None
        self.initialized = False
        self.instances, self.handles, self.scope_info = {}, {}, {}

    def __enter__(self):
        home = Path(self.config["verdi_home"])
        require((home / "share/NPI/python/pynpi").is_dir(), f"Verdi Python NPI not found under {home}; set --verdi-home")
        os.environ["VERDI_HOME"] = str(home)
        sys.path.insert(0, str(home / "share/NPI/python"))
        try:
            from pynpi import cov, npisys
        except (ImportError, OSError) as exc:
            raise ToolError(f"Cannot load Verdi NPI for this Python/environment: {exc}") from exc
        self.cov, self.npisys = cov, npisys
        require(npisys.init(["uncover-extract"]) == 1, "NPI initialization/license checkout failed; inspect execution.log")
        self.initialized = True
        try:
            self.db = cov.open(str(self.vdb))
            require(self.db is not None, "NPI could not open the VDB copy")
            tests = self.db.test_handles()
            names = {test.name().rsplit("/", 1)[-1]: test for test in tests}
            require(len(names) == len(tests), "Ambiguous native test names")
            self.available_tests = sorted(names)
            requested = self.config["tests"]
            if self.config["all_tests"]:
                requested = sorted(names)
            elif requested is None:
                require(len(names) == 1, f"VDB has {len(names)} tests {sorted(names)}; use --tests or --all-tests")
                requested = sorted(names)
            require(requested and set(requested) <= set(names),
                    f"Unknown/empty test selection {requested}; available tests: {sorted(names)}")
            self.selected_tests = sorted(set(requested))
            self.test = names[self.selected_tests[0]]
            for selected in self.selected_tests[1:]:
                self.test = cov.merge_test(self.test, names[selected])
                require(self.test is not None, f"Native test merge failed at {selected}; incompatible model or data")
            for path in self.config["exclude_files"]:
                require(self.test.load_exclude_file(path) == 1, f"Cannot load external exclusion file {path}")
            self.definitions, self.models, self.warnings = xml_hints(self.vdb)
            stack = list(self.db.instance_handles())
            while stack:
                instance = stack.pop()
                path = instance.full_name()
                require(path not in self.instances, f"Duplicate native instance path {path}")
                self.instances[path] = instance
                stack.extend(instance.instance_handles())
            for root in self.config["instance_roots"]:
                require(root in self.instances, f"Unknown instance root {root}")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.initialized:
            self.npisys.end()
            self.initialized = False

    def handle(self, path, metric):
        key = path, metric
        if key not in self.handles:
            self.handles[key] = getattr(self.instances[path], METRICS[metric][0])()
        return self.handles[key]

    def bins(self, path, metric, missing_only=False):
        return walk_bins(self.handle(path, metric), METRICS[metric][1], self.test,
                         missing_only=missing_only, release=self.cov.release_handle)

    def release_parents(self, parents):
        for parent in reversed(parents[1:]):
            self.cov.release_handle(parent)

    def info(self, path, metric):
        key = path, metric
        if key not in self.scope_info:
            inst = self.instances[path]
            hint = self.definitions.get(path, {})
            checksum = self.models.get(metric, {}).get(path)
            model = {"definition_id": hint.get("definition_id"),
                     "definition_checksum": hint.get("definition_checksum"), "metric_checksum": checksum}
            comparable = all(value is not None for value in model.values())
            self.scope_info[key] = {"module_name": inst.def_name(), **hint, **model,
                "model_key": stable_id(model) if comparable else None,
                "sources": hint.get("sources", [inst.file_name()] if inst.file_name() else [])}
        return self.scope_info[key]


class Inventory:
    """Spill candidates/results to disk; full Toggle extraction need not fit in RAM."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE candidates (metric TEXT, instance TEXT, source TEXT, line INTEGER, locator TEXT, record TEXT)")
        self.db.execute("CREATE TABLE selected (seq INTEGER PRIMARY KEY, metric TEXT, record TEXT)")
        self.db.execute("CREATE TABLE special (record TEXT)")

    def add(self, record):
        key = ".".join(f"{step['index']:012d}" for step in record["native_locator"])
        self.db.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?)", (record["metric"], record["instance_path"],
            record["source_file"] or "", record["start_line"] or 0, key, json.dumps(record, separators=(",", ":"))))

    def candidates(self, metric, limit=None):
        sql = "SELECT record FROM candidates WHERE metric=? ORDER BY source,instance,line,locator"
        params = [metric]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        for row in self.db.execute(sql, params):
            yield json.loads(row[0])

    def selected(self, metric=None):
        sql, params = ("SELECT record FROM selected ORDER BY seq", ()) if metric is None else (
            "SELECT record FROM selected WHERE metric=? ORDER BY seq", (metric,))
        for row in self.db.execute(sql, params):
            yield json.loads(row[0])


def scan(session, config, inventory, mapper, database_id):
    summary, capabilities = {}, {}
    requested = config["metrics"]
    metrics = requested or list(METRICS)
    test_ref = stable_id(session.selected_tests)
    for metric in metrics:
        scopes = []
        for path in sorted(session.instances):
            if not in_scope(path, config["instance_roots"]):
                continue
            handle = session.handle(path, metric)
            if handle is not None:
                children = handle.child_handles()
                if children:
                    scopes.append(path)
                for child in children:
                    session.cov.release_handle(child)
        # XML files only distinguish collected-empty versus absent; never decode hit bits.
        shape_present = (session.vdb / f"snps/coverage/db/shape/{metric}.verilog.shape.xml").exists()
        available = bool(scopes) or shape_present
        capabilities[metric] = {"status": "available" if available else "not_collected_or_unavailable",
                                "native_scopes": len(scopes), "native_reader": "Verdi NPI"}
        require(available or not requested, f"Requested metric {metric} is not collected/readable in the selected instance scope")
        if not available:
            continue
        counters = Counter()
        scope_totals = []
        limited = []
        limit = config["limit"]
        for number, path in enumerate(scopes, 1):
            root = session.handle(path, metric)
            covered, coverable = native_totals(root, session.test, session.cov.release_handle)
            require(0 <= covered <= coverable, f"Invalid native totals for {path}/{metric}: {covered}/{coverable}")
            counters["native_covered"] += covered
            counters["native_coverable"] += coverable
            scope_totals.append({"instance_path": path, "covered": covered, "coverable": coverable})
            info = session.info(path, metric)
            for node, trail, parents in session.bins(path, metric, missing_only=not config["include_other_states"]):
                counters["visited_atomic_bins"] += 1
                current = state(node, session.test)
                counters["visited_" + current["status"]] += 1
                if current["status"] != "uncovered" and not config["include_other_states"]:
                    continue
                source, line = location(parents)
                candidate_key = (source or "", path, line or 0, tuple(step["index"] for step in trail))
                if current["status"] == "uncovered":
                    counters["uncovered_candidates"] += 1
                    # Count every candidate, but retain only the deterministic prefix when limited.
                    if limit is not None and len(limited) >= limit and candidate_key >= limited[-1][0]:
                        continue
                identity = [database_id, test_ref, path, metric, list(trail)]
                record = {"target_id": stable_id(identity), "database_id": database_id, "test_scope_ref": test_ref,
                    "metric": metric, "scope_kind": "instance_self", "instance_path": path,
                    "module_name": info["module_name"], "model_id": info["model_key"],
                    "source_file": source, "start_line": line, "end_line": line,
                    "source_mapping": mapper.map(source), "native_locator": list(trail),
                    "target": {"bin_name": node.name(), "objects": list(trail),
                               "description": " / ".join(str(d["name"]) for d in trail)}, **current}
                if metric == "tgl":
                    record["target"]["direction"] = node.name()
                    record["target"]["signal"] = next((p.name() for p in reversed(parents[:-1]) if p.type() in ("npiCovSignalBit", "npiCovSignal")), None)
                if current["status"] == "uncovered":
                    if limit is None:
                        inventory.add(record)
                    else:
                        insort(limited, (candidate_key, record), key=lambda pair: pair[0])
                        if len(limited) > limit:
                            limited.pop()
                elif config["include_other_states"] and current["status"] not in ("covered",):
                    inventory.db.execute("INSERT INTO special VALUES (?)", (json.dumps(record, separators=(",", ":")),))
            if number % 50 == 0:
                inventory.db.commit()
                print(f"{metric}: scanned {number}/{len(scopes)} instance scopes, {counters['uncovered_candidates']} holes", flush=True)
        for _, record in limited:
            inventory.add(record)
        inventory.db.commit()
        summary[metric] = {**dict(counters), "uncovered_candidates": counters["uncovered_candidates"],
            "native_covered": counters["native_covered"], "native_coverable": counters["native_coverable"],
            "scope_count": len(scopes), "scopes": scope_totals,
            "nonstandard_state_inventory": "complete" if config["include_other_states"] else "not_requested; fully scored/zero-count subtrees pruned"}
        print(f"{metric}: {counters['uncovered_candidates']} uncovered atomic targets", flush=True)
    require(summary, "No implemented Verilog/SystemVerilog metrics are available in this VDB/scope")
    return summary, capabilities
