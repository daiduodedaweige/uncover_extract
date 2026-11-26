"""Native reads and optional XML identity hints, isolated to the worker process."""
from __future__ import annotations

import gzip
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from .common import METRICS, ToolError, require, stable_id, walk_bins


def read_xml(path):
    data = path.read_bytes()
    return ET.fromstring(gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data)


def xml_hints(vdb):
    """No hit data are decoded here. Unknown metadata disables optional source hints."""
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
                "parameters": None, "parameter_evidence": "native definition metadata; explicit parameter values unavailable"}
        for metric in METRICS:
            path = base / f"shape/{metric}.verilog.info.xml"
            if path.exists():
                metric_models[metric] = {i.get("name"): i.get("chksum") for i in read_xml(path).findall("instance_data")}
    except (OSError, ET.ParseError, KeyError, ToolError) as exc:
        warnings.append(f"XML identity hints unavailable: {exc}; coverage facts remain readable")
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

    def bins(self, path, metric):
        return walk_bins(self.handle(path, metric), METRICS[metric][1], release=self.cov.release_handle)

    def info(self, path, metric):
        key = path, metric
        if key not in self.scope_info:
            inst = self.instances[path]
            hint = self.definitions.get(path, {})
            checksum = self.models.get(metric, {}).get(path)
            model = {"definition_id": hint.get("definition_id"),
                     "definition_checksum": hint.get("definition_checksum"), "metric_checksum": checksum}
            known_model = all(value is not None for value in model.values())
            self.scope_info[key] = {"module_name": inst.def_name(), **hint, **model,
                "model_key": stable_id(model) if known_model else None,
                "sources": hint.get("sources", [inst.file_name()] if inst.file_name() else [])}
        return self.scope_info[key]
