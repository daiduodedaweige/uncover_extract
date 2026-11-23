"""One native walk per instance, shared model targets and lossless persistent facts."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import time

from .common import METRICS, display_status, in_scope, is_excluded, location, native_totals, require, stable_id, state_from_facts, write_json
from .output import cell, source_cells, target_description


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sql_int(value):
    return value if -(1 << 63) <= value < (1 << 63) else str(value)


class FactStore:
    def __init__(self, path, batch_size=256):
        self.path = Path(path)
        # Scratch and published files are owned by this run, never reused in place.
        with self.path.open("xb"):
            pass
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA cache_size=-16384")
        self.db.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE instances (id INTEGER PRIMARY KEY, path TEXT UNIQUE, selected INTEGER);
            CREATE TABLE models (id INTEGER PRIMARY KEY, group_key TEXT UNIQUE, metric TEXT, info TEXT);
            CREATE TABLE scopes (model INTEGER, instance INTEGER, covered, coverable, atomic_targets INTEGER,
                PRIMARY KEY(model,instance)) WITHOUT ROWID;
            CREATE TABLE sources (id INTEGER PRIMARY KEY, path TEXT, mapping TEXT);
            CREATE TABLE targets (id INTEGER PRIMARY KEY, model INTEGER, ordinal INTEGER, source INTEGER,
                line INTEGER, locator BLOB, trail TEXT, representative INTEGER, selected INTEGER);
            CREATE UNIQUE INDEX target_identity ON targets(model,source,locator);
            CREATE UNIQUE INDEX reference_order ON targets(model,ordinal);
            CREATE INDEX target_order ON targets(model,selected,id);
            CREATE TABLE states (instance INTEGER, target INTEGER, covered, coverable, raw,
                PRIMARY KEY(instance,target)) WITHOUT ROWID;
            CREATE TABLE classifications (target INTEGER PRIMARY KEY, category TEXT, incomparable INTEGER);
            CREATE TABLE patterns (id TEXT PRIMARY KEY, model INTEGER, source INTEGER, category TEXT,
                distribution TEXT, incompatible TEXT, target_count INTEGER);
            CREATE TABLE findings (target INTEGER PRIMARY KEY, pattern TEXT);
            CREATE INDEX pattern_findings ON findings(pattern,target);
        """)
        self.batch_size = min(max(1, batch_size), 500)
        self.next_target = 0
        self.source_ids = {}
        self.pending_targets, self.pending_states = [], []
        self.pending_selected = []
        self.stats = Counter()

    def source(self, source, mapper):
        if source not in self.source_ids:
            number = len(self.source_ids) + 1
            self.source_ids[source] = number
            self.db.execute("INSERT INTO sources VALUES (?,?,?)", (number, source, packed(mapper.map(source))))
        return self.source_ids[source]

    def add_target(self, model, ordinal, source, line, trail, representative, selected):
        self.next_target += 1
        identity = bytes.fromhex(stable_id(trail))
        self.pending_targets.append((self.next_target, model, ordinal, source, line, identity,
                                     packed(trail), representative, int(selected)))
        self.stats["target_descriptions_serialized"] += 1
        return self.next_target

    def reference(self, model):
        # Each page is decoded once per instance. Matching walks need no locator
        # hashing or target-existence queries, but compare every actual descriptor.
        last = -1
        while rows := self.db.execute("SELECT id,ordinal,source,trail FROM targets "
                "WHERE model=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                (model, last, self.batch_size)).fetchall():
            for target, ordinal, source, trail in rows:
                yield target, source, json.loads(trail)
            last = rows[-1][1]

    def fallback_target(self, model, source, line, trail, instance, selected):
        self.flush()
        locator = bytes.fromhex(stable_id(trail))
        row = self.db.execute("SELECT id FROM targets WHERE model=? AND source=? AND locator=?",
                              (model, source, locator)).fetchone()
        if row is None:
            return self.add_target(model, None, source, line, trail, instance, selected)
        if selected:
            self.pending_selected.append((row[0],))
        return row[0]

    def add_state(self, instance, target, facts):
        self.pending_states.append((instance, target, *(sql_int(v) for v in facts)))
        self.stats["native_atomic_state_reads"] += 1
        if len(self.pending_states) >= self.batch_size:
            self.flush()

    def flush(self, commit=False):
        if self.pending_targets:
            self.db.executemany("INSERT INTO targets VALUES (?,?,?,?,?,?,?,?,?)", self.pending_targets)
            self.pending_targets.clear()
        if self.pending_selected:
            self.db.executemany("UPDATE targets SET selected=1 WHERE id=?", self.pending_selected)
            self.pending_selected.clear()
        if self.pending_states:
            self.db.executemany("INSERT INTO states VALUES (?,?,?,?,?)", self.pending_states)
            self.pending_states.clear()
            self.stats["state_insert_batches"] += 1
        if commit:
            self.db.commit()

    def states(self, instances, targets):
        marks = ",".join("?" for _ in targets)
        result = {}
        for instance in instances:
            result[instance] = {target: tuple(int(v) for v in facts) for target, *facts in
                self.db.execute(f"SELECT target,covered,coverable,raw FROM states "
                                f"WHERE instance=? AND target IN ({marks})", (instance, *targets))}
            self.stats["state_query_batches"] += 1
        return result

    def close(self):
        self.db.close()

    def discard_excluded(self):
        """Keep exclusions for alignment, then remove them from published facts."""
        self.flush()
        db = self.db
        db.create_function("is_excluded", 1, lambda raw: is_excluded(int(raw)))
        db.executescript("""
            CREATE TEMP TABLE excluded_states AS
                SELECT instance,target FROM states WHERE is_excluded(raw);
            CREATE TEMP TABLE excluded_targets (target INTEGER PRIMARY KEY);
            INSERT INTO excluded_targets SELECT DISTINCT target FROM excluded_states;
            DELETE FROM states WHERE (instance,target) IN (SELECT instance,target FROM excluded_states);
            CREATE TEMP TABLE retained_targets (target INTEGER PRIMARY KEY, representative INTEGER, selected INTEGER);
            INSERT INTO retained_targets
                SELECT t.id,MIN(s.instance),MAX(i.selected)
                FROM excluded_targets e JOIN targets t ON t.id=e.target
                JOIN scopes sc ON sc.model=t.model
                JOIN states s ON s.instance=sc.instance AND s.target=t.id
                JOIN instances i ON i.id=s.instance GROUP BY t.id;
            DELETE FROM targets WHERE id IN (SELECT target FROM excluded_targets)
                AND id NOT IN (SELECT target FROM retained_targets);
            UPDATE targets SET
                representative=CASE WHEN EXISTS (SELECT 1 FROM states s
                    WHERE s.instance=targets.representative AND s.target=targets.id)
                    THEN representative ELSE (SELECT representative FROM retained_targets r WHERE r.target=targets.id) END,
                selected=(SELECT selected FROM retained_targets r WHERE r.target=targets.id)
                WHERE id IN (SELECT target FROM retained_targets);
            DROP TABLE retained_targets;
            DROP TABLE excluded_targets;
            DROP TABLE excluded_states;
        """)


def analyze(session, config, stage, work, mapper, database_id, timings):
    store = FactStore(work / "coverage_facts.sqlite", config.get("batch_size", 256))
    try:
        result = _analyze(session, config, store, mapper, database_id, timings)
        store.discard_excluded()
        store.flush(commit=True)
    finally:
        store.close()
    tick = time.perf_counter()
    shutil.copy2(store.path, stage / "coverage_facts.sqlite")
    timings["facts_publish_seconds"] = time.perf_counter() - tick
    return result


def _analyze(session, config, store, mapper, database_id, timings):
    db = store.db
    paths = sorted(session.instances)
    instance_ids = {path: i + 1 for i, path in enumerate(paths)}
    selected = {p for p in paths if in_scope(p, config["instance_roots"])}
    db.executemany("INSERT INTO instances VALUES (?,?,?)", ((i, p, int(p in selected)) for p, i in instance_ids.items()))
    metadata = {"schema_version": "1.0", "database_id": database_id,
                "test_scope_ref": stable_id(session.selected_tests), "selected_tests": session.selected_tests,
                "configuration": config,
                "comparison_basis": "definition/metric model identity + every actual native locator descriptor"}
    db.executemany("INSERT INTO metadata VALUES (?,?)", ((k, packed(v)) for k, v in metadata.items()))
    requested = config["metrics"]
    summary, capabilities, models, references = {}, {}, {}, defaultdict(set)
    index_tick = time.perf_counter()
    for path in paths:
        sources = session.definitions.get(path, {}).get("sources", [])
        if not sources:
            source = session.instances[path].file_name()
            sources = [source] if source else []
        for source in sources:
            references[source].add(path)
    for metric in requested or METRICS:
        scoped = []
        for path in paths:
            handle = session.handle(path, metric)
            if handle is None:
                continue
            children = handle.child_handles()
            populated = bool(children)
            for child in children:
                session.cov.release_handle(child)
            if path in selected and populated:
                scoped.append(path)
        shape_present = (session.vdb / f"snps/coverage/db/shape/{metric}.verilog.shape.xml").exists()
        available = bool(scoped) or shape_present
        capabilities[metric] = {"status": "available" if available else "not_collected_or_unavailable",
                                "native_scopes": len(scoped), "native_reader": "Verdi NPI"}
        require(available or not requested, f"Requested metric {metric} is not collected/readable in the selected instance scope")
        if not available:
            continue
        summary[metric] = {"native_covered": 0, "native_coverable": 0, "uncovered_candidates": 0,
            "visited_atomic_bins": 0, "output_count": 0, "scope_count": len(scoped), "scopes": [],
            "nonstandard_state_inventory": "non-excluded original facts in coverage_facts.sqlite"}
        active = set()
        infos = {path: session.info(path, metric) for path in paths}
        for path in scoped:
            info = infos[path]
            active.add(("model", info["model_key"]) if info["model_key"] is not None else ("instance", path))
        for path in paths:
            info = infos[path]
            identity = ("model", info["model_key"]) if info["model_key"] is not None else ("instance", path)
            if identity not in active:
                continue
            key = stable_id([metric, identity])
            if key not in models:
                number = len(models) + 1
                models[key] = {"id": number, "metric": metric, "info": info, "paths": [], "scoped": set(scoped)}
                db.execute("INSERT INTO models VALUES (?,?,?,?)", (number, key, metric, packed(info)))
            models[key]["paths"].append(path)
    require(summary, "No implemented Verilog/SystemVerilog metrics are available in this VDB/scope")
    store.stats["index_seconds"] = time.perf_counter() - index_tick
    scan_tick, last_progress = time.perf_counter(), time.perf_counter()
    for model in models.values():
        mid, metric = model["id"], model["metric"]
        # A selected instance establishes the canonical order; outside-root peers
        # are scanned too, but their additional targets do not expand the scope.
        ordered = sorted(model["paths"], key=lambda p: (p not in selected, p))
        for instance_number, path in enumerate(ordered):
            iid = instance_ids[path]
            root = session.handle(path, metric)
            covered, coverable = native_totals(root, session.test, session.cov.release_handle) if root is not None else (0, 0)
            require(0 <= covered <= coverable, f"Invalid native totals for {path}/{metric}: {covered}/{coverable}")
            count = 0
            reference = iter(store.reference(mid)) if instance_number else None
            if root is not None:
                for ordinal, (node, trail, parents) in enumerate(session.bins(path, metric)):
                    source, line = location(parents, trail)
                    sid = store.source(source, mapper)
                    expected = next(reference, None) if reference is not None else None
                    if reference is None:
                        target = store.add_target(mid, ordinal, sid, line, trail, iid, path in selected)
                    elif expected is not None and expected[1] == sid and expected[2] == list(trail):
                        target = expected[0]
                        store.stats["verified_reference_matches"] += 1
                    else:
                        store.stats["reference_mismatches"] += 1
                        target = store.fallback_target(mid, sid, line, trail, iid, path in selected)
                    facts = node.covered(session.test), node.coverable(session.test), node.status(session.test)
                    store.add_state(iid, target, facts)
                    excluded = is_excluded(facts[2])
                    count += not excluded
                    if path in model["scoped"] and not excluded:
                        counts = summary[metric]
                        status = display_status(*facts)
                        counts["visited_atomic_bins"] += 1
                        counts["visited_" + status] = counts.get("visited_" + status, 0) + 1
                        counts["uncovered_candidates"] += status == "uncovered"
                    if (ordinal + 1) % store.batch_size == 0:
                        # Bound rollback journals as well as Python buffers.
                        if (ordinal + 1) % (store.batch_size * 32) == 0:
                            store.flush(commit=True)
                        now = time.perf_counter()
                        if now - last_progress >= 5:
                            print(f"comparison: scanning {path}/{metric}, {store.stats['native_atomic_state_reads']} states, "
                                  f"{now - scan_tick:.1f}s", flush=True)
                            last_progress = now
            store.flush()
            db.execute("INSERT INTO scopes VALUES (?,?,?,?,?)", (mid, iid, sql_int(covered), sql_int(coverable), count))
            if path in model["scoped"]:
                counts = summary[metric]
                counts["native_covered"] += covered
                counts["native_coverable"] += coverable
                counts["scopes"].append({"instance_path": path, "covered": covered, "coverable": coverable})
            store.flush(commit=True)
    timings["fact_scan_seconds"] = time.perf_counter() - scan_tick
    print(f"comparison: scan complete, {store.stats['native_atomic_state_reads']} states, "
          f"{store.next_target} shared targets", flush=True)
    compare_tick = time.perf_counter()
    total, differences, incomparables = 0, 0, 0
    source_paths = {sid: source for source, sid in store.source_ids.items()}
    for model in models.values():
        mid = model["id"]
        peers = sorted(model["paths"])
        ids = [instance_ids[p] for p in peers]
        peer_set = set(peers)
        source_errors = {}
        last = 0
        while rows := db.execute("SELECT id,source FROM targets WHERE model=? AND selected=1 AND id>? "
                "ORDER BY id LIMIT ?", (mid, last, store.batch_size)).fetchall():
            facts = store.states(ids, [r[0] for r in rows])
            categories, findings, patterns = [], [], {}
            for target, source in rows:
                distribution, missing, statuses = [], [], set()
                selected_present = False
                for path, iid in zip(peers, ids):
                    value = facts[iid].get(target)
                    if value is None:
                        missing.append({"instance_path": path, "reason": "native target descriptor/layout differs or target absent"})
                    elif not is_excluded(value[2]):
                        status = display_status(*value)
                        distribution.append((iid, status))
                        statuses.add(status)
                        selected_present |= path in selected
                if not selected_present:
                    continue
                if source not in source_errors:
                    source_errors[source] = [{"instance_path": path,
                        "reason": "definition/model identity differs or unavailable"}
                        for path in sorted(references[source_paths[source]] - peer_set)]
                errors = missing + source_errors[source]
                errors.sort(key=lambda r: r["instance_path"])
                mixed = len(statuses) > 1
                category = "different" if mixed else ({"covered": "all_covered", "uncovered": "common_uncovered"}.get(
                    next(iter(statuses), None), "common_special"))
                categories.append((target, category, int(bool(errors))))
                total += 1
                differences += mixed
                incomparables += bool(errors)
                if mixed or errors or category == "common_special":
                    distribution = [{"instance": iid, "status": status} for iid, status in distribution]
                    signature = [mid, source, category, distribution, errors]
                    pid = stable_id(signature)
                    findings.append((target, pid))
                    if pid not in patterns:
                        patterns[pid] = [pid, mid, source, category, packed(distribution), packed(errors), 0]
                    patterns[pid][-1] += 1
            db.executemany("INSERT INTO classifications VALUES (?,?,?)", categories)
            db.executemany("INSERT INTO findings VALUES (?,?)", findings)
            db.executemany("INSERT INTO patterns VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                           "SET target_count=target_count+excluded.target_count", patterns.values())
            db.commit()
            last = rows[-1][0]
            now = time.perf_counter()
            if now - last_progress >= 5:
                print(f"comparison: classified {total} unique targets, {differences} differences", flush=True)
                last_progress = now
    timings["fact_comparison_seconds"] = time.perf_counter() - compare_tick
    patterns = db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    for metric, counts in summary.items():
        counts["difference_count"] = db.execute("SELECT COUNT(*) FROM classifications c JOIN targets t "
            "ON t.id=c.target JOIN models m ON m.id=t.model WHERE m.metric=? AND c.category='different'",
            (metric,)).fetchone()[0]
        counts["output_count"] = db.execute("SELECT COUNT(*) FROM findings f JOIN targets t "
            "ON t.id=f.target JOIN models m ON m.id=t.model WHERE m.metric=?", (metric,)).fetchone()[0]
    report = {"unique_model_targets_checked": total,
        "targets_with_state_differences": differences, "targets_with_incomparable_instances": incomparables,
        "reported_patterns": patterns, "scope": "selected model targets; same-model peers include instances outside roots",
        "facts": "coverage_facts.sqlite"}
    return summary, capabilities, report, dict(store.stats)


class FactsReader:
    """Read-only facts API: no NPI or simulation license is needed for exports."""
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.metadata = {k: json.loads(v) for k, v in self.db.execute("SELECT key,value FROM metadata")}
        self.instances = dict(self.db.execute("SELECT id,path FROM instances"))

    def close(self):
        self.db.close()

    def uncovered_instances(self):
        """Summarize every selected instance's holes, including common holes."""
        selected = dict(self.db.execute("SELECT id,path FROM instances WHERE selected=1"))
        counts = Counter()
        # Common holes without missing/incompatible peers have no finding rows.
        # Count only surviving instance states: peers may omit individual bins.
        for model, instance, count in self.db.execute(
                "SELECT t.model,s.instance,COUNT(*) FROM classifications c JOIN targets t ON t.id=c.target "
                "JOIN scopes sc ON sc.model=t.model JOIN instances i ON i.id=sc.instance AND i.selected=1 "
                "JOIN states s ON s.instance=sc.instance AND s.target=t.id "
                "WHERE c.category='common_uncovered' AND c.incomparable=0 GROUP BY t.model,s.instance"):
            counts[model, instance] = count
        # Findings retain exact distributions, including partially aligned holes.
        for model, distribution, count in self.db.execute("SELECT model,distribution,target_count FROM patterns"):
            for entry in json.loads(distribution):
                if entry["instance"] in selected and entry["status"] == "uncovered":
                    counts[model, entry["instance"]] += count
        names = {model: json.loads(info).get("module_name")
                 for model, info in self.db.execute("SELECT id,info FROM models")}
        instances = Counter()
        for (model, instance), count in counts.items():
            instances[names[model], selected[instance]] += count
        return [{"module_name": name, "instance_path": path, "uncovered_count": count}
                for (name, path), count in sorted(instances.items(), key=lambda item: (item[0][0] or "", item[0][1]))
                if count]

    def details(self, kind="differences"):
        query = ("SELECT t.id,t.model,t.source,t.line,t.trail,t.representative,m.metric,m.info "
                 "FROM targets t JOIN models m ON m.id=t.model ")
        if kind == "differences":
            query += "JOIN findings f ON f.target=t.id "
        elif kind == "common-uncovered":
            query += "JOIN classifications c ON c.target=t.id AND c.category='common_uncovered' "
        query += "WHERE t.selected=1 ORDER BY t.id"
        for target, model, source, line, trail_json, representative, metric, info_json in self.db.execute(query):
            trail, info = json.loads(trail_json), json.loads(info_json)
            source_path, mapping = self.db.execute("SELECT path,mapping FROM sources WHERE id=?", (source,)).fetchone()
            # states are indexed by instance: scope queries avoid a full-table scan per target.
            peers = []
            values = []
            for (iid,) in self.db.execute("SELECT instance FROM scopes WHERE model=? ORDER BY instance", (model,)):
                row = self.db.execute("SELECT covered,coverable,raw FROM states WHERE instance=? AND target=?",
                                      (iid, target)).fetchone()
                if row is not None:
                    values.append((iid, tuple(int(v) for v in row)))
            for iid, facts in values:
                peers.append({"instance_path": self.instances[iid], **state_from_facts(*facts)})
            pattern = self.db.execute("SELECT p.incompatible FROM findings f JOIN patterns p ON p.id=f.pattern "
                                      "WHERE f.target=?", (target,)).fetchone()
            errors = json.loads(pattern[0]) if pattern else []
            base = {"metric": metric, "instance_path": self.instances[representative], "source_file": source_path,
                    "start_line": line, "native_locator": trail}
            if kind == "holes":
                for iid, facts in values:
                    if display_status(*facts) != "uncovered":
                        continue
                    selected = self.db.execute("SELECT selected FROM instances WHERE id=?", (iid,)).fetchone()[0]
                    if not selected:
                        continue
                    path = self.instances[iid]
                    target_info = {"bin_name": trail[-1]["name"] if trail else None,
                                   "objects": trail, "description": " / ".join(str(s["name"]) for s in trail)}
                    if metric == "tgl":
                        target_info.update(direction=target_info["bin_name"], signal=next((s["name"] for s in reversed(trail[:-1])
                            if s["type"] in ("npiCovSignalBit", "npiCovSignal")), None))
                    yield {**base, "instance_path": path, "target_id": stable_id([
                        self.metadata["database_id"], self.metadata["test_scope_ref"], path, metric, trail]),
                        "database_id": self.metadata["database_id"], "test_scope_ref": self.metadata["test_scope_ref"],
                        "scope_kind": "instance_self", "end_line": line,
                        "module_name": info.get("module_name"), "model_id": info.get("model_key"),
                        "source_mapping": json.loads(mapping), "target": target_info,
                        **state_from_facts(*facts)}
            else:
                own = next(v for iid, v in values if iid == representative)
                yield {**base, **state_from_facts(*own), "same_rtl_coverage": {
                    "comparison_basis": self.metadata["comparison_basis"],
                    "instances": peers, "incomparable_instances": errors,
                    "instance_count": len(peers) + len(errors), "comparable_instance_count": len(peers),
                    "same_target_differs": len({p["status"] for p in peers}) > 1}}


def write_report(stage, manifest):
    reader = FactsReader(stage / "coverage_facts.sqlite")
    try:
        _write_report(stage, manifest, reader)
    finally:
        reader.close()


def _write_report(stage, manifest, reader):
    db = reader.db
    models = []
    for mid, key, metric, info_json in db.execute("SELECT id,group_key,metric,info FROM models ORDER BY id"):
        info = json.loads(info_json)
        counts = dict(db.execute("SELECT c.category,COUNT(*) FROM classifications c JOIN targets t ON t.id=c.target "
                                 "WHERE t.model=? GROUP BY c.category", (mid,)))
        if not counts:
            continue
        incompatible = db.execute("SELECT COUNT(*) FROM classifications c JOIN targets t ON t.id=c.target "
                                   "WHERE t.model=? AND c.incomparable=1", (mid,)).fetchone()[0]
        instances = [reader.instances[r[0]] for r in db.execute(
            "SELECT instance FROM scopes WHERE model=? AND atomic_targets>0 ORDER BY instance", (mid,))]
        models.append({"id": mid, "group_key": key, "metric": metric, "module_name": info.get("module_name"),
                       "model_id": info.get("model_key"), "instances": instances, "counts": counts,
                       "incomparable_targets": incompatible,
                       "comparison_status": "identity_unavailable" if info.get("model_key") is None else (
                           "single_instance" if len(instances) < 2 else "comparable")})
    sources = {sid: {"path": path, "mapping": json.loads(mapping)}
               for sid, path, mapping in db.execute("SELECT id,path,mapping FROM sources")}
    write_json(stage / "module_summary.json", {"schema_version": "1.0", "models": models,
                                               "instances": reader.instances, "sources": sources})
    with gzip.open(stage / "difference_patterns.jsonl.gz", "wt", encoding="utf-8") as output:
        for pid, mid, source, category, distribution, errors, count in db.execute("SELECT * FROM patterns ORDER BY model,id"):
            output.write(packed({"id": pid, "model": mid, "source": source, "category": category,
                "instances": json.loads(distribution), "incomparable_instances": json.loads(errors),
                "target_count": count}) + "\n")
    with gzip.open(stage / "differences.jsonl.gz", "wt", encoding="utf-8") as output, \
         (stage / "differences.csv").open("w", encoding="utf-8-sig", newline="") as csv_output:
        writer = csv.writer(csv_output)
        writer.writerow(["target", "model", "metric", "source_file", "line", "description", "pattern", "category"])
        for target, model, metric, source, line, trail, pattern, category in db.execute(
                "SELECT t.id,t.model,m.metric,s.path,t.line,t.trail,f.pattern,c.category "
                "FROM findings f JOIN targets t ON t.id=f.target JOIN models m ON m.id=t.model "
                "JOIN sources s ON s.id=t.source JOIN classifications c ON c.target=t.id ORDER BY t.id"):
            description = target_description(metric, json.loads(trail))
            output.write(packed({"target": target, "model": model, "metric": metric, "source_file": source,
                                "line": line, "description": description, "pattern": pattern, "category": category}) + "\n")
            writer.writerow([target, model, metric, source, line, description, pattern, category])
    with (stage / "report.md").open("w", encoding="utf-8") as report:
        report.write("# 存在未覆盖点的模块实例\n\n")
        uncovered = reader.uncovered_instances()
        if uncovered:
            report.write("| 模块 | 实例 | 未覆盖点数 |\n| --- | --- | ---: |\n")
            for row in uncovered:
                report.write(f"| {cell(row['module_name'])} | `{cell(row['instance_path'])}` | {row['uncovered_count']} |\n")
        else:
            report.write("没有发现未覆盖的模块实例。\n")
        report.write("\n## 分类依据\n\n")
        report.write("按同一模块定义及覆盖模型中的同一原子覆盖点比较各实例，逐位、逐方向、逐 bin 判断，"
                     "不按模块覆盖率或源码行合并。已覆盖表示该点已命中，未覆盖表示该点参与计分但尚未命中。\n\n")
        report.write("| 分类 | 判断标准 |\n| --- | --- |\n"
                     "| 全体已覆盖 | 所有可比实例均已覆盖，只计入汇总 |\n"
                     "| 共同未覆盖 | 所有可比实例均未覆盖，只计入汇总及上方未覆盖点数 |\n"
                     "| 实例覆盖不同 | 可比实例在同一覆盖点的状态不同，列入明细 |\n"
                     "| 共同特殊状态 | 可比实例均为相同的不可达、不计分、非法或未知状态，列入明细 |\n\n")
        report.write("目标缺失、定位不同或模型身份不足时标记为不可比，不算作未覆盖；"
                     "存在不可比对照的覆盖点也列入明细。每组明细共用一份完整的实例状态分布。\n")
        report.write("\n## 覆盖点明细\n\n")
        model_names = {model["id"]: (model["module_name"], model["metric"]) for model in models}
        labels = {"different": "实例覆盖不同", "all_covered": "已覆盖但存在不可比对照",
                  "common_uncovered": "共同未覆盖但存在不可比对照", "common_special": "共同特殊状态"}
        state_labels = {"covered": "已覆盖", "uncovered": "未覆盖",
                        "unreachable": "不可达", "illegal": "非法", "not_scored": "不计分", "unknown": "未知"}
        for pid, mid, source, category, distribution, errors, count in db.execute("SELECT * FROM patterns ORDER BY model,id"):
            name, metric = model_names[mid]
            report.write(f"### {cell(name)} / {metric} / {labels[category]} / {count} 个覆盖点\n\n")
            report.write(f"模型组 {mid}，分组编号：`{pid}`。\n\n")
            report.write("| 实例 | 覆盖情况 |\n| --- | --- |\n")
            for entry in json.loads(distribution):
                path = reader.instances[entry["instance"]]
                status = state_labels.get(entry["status"], entry["status"])
                report.write(f"| `{cell(path)}` | {cell(status)} |\n")
            for error in json.loads(errors):
                report.write(f"| `{cell(error['instance_path'])}` | 不可比：{cell(error['reason'])} |\n")
            report.write("\n")
            show_target = metric != "line"
            target_header = " 信号 / 覆盖点 / 方向 |" if show_target else ""
            target_separator = " --- |" if show_target else ""
            report.write(f"| 目标编号 | 源码文件 | 行号 |{target_header}\n"
                         f"| ---: | --- | ---: |{target_separator}\n")
            for target, source, path, line, trail in db.execute("SELECT t.id,t.source,s.path,t.line,t.trail FROM findings f "
                    "JOIN targets t ON t.id=f.target JOIN sources s ON s.id=t.source WHERE f.pattern=? ORDER BY t.id", (pid,)):
                source_text, line_text = source_cells(path, line, sources[source]["mapping"], report_dir=stage)
                target_text = f" {cell(target_description(metric, json.loads(trail)))} |" if show_target else ""
                report.write(f"| {target} | {source_text} | {line_text} |{target_text}\n")
            report.write("\n")
        report.write("状态差异定位了实例和覆盖点；具体原因需结合连接、激励或波形分析。\n\n")
        report.write("[模块汇总](module_summary.json) · [差异 CSV](differences.csv) · "
                     "[完整紧凑事实](coverage_facts.sqlite) · [运行摘要](analysis_manifest.json)\n")
