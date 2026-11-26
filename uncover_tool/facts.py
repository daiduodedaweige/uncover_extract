"""Lossless native facts and read-only per-instance queries."""
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from .common import METRICS, display_status, is_excluded, require, stable_id, state_from_facts
from .output import locator_sort, natural_key, target_description

FACTS_VERSION = "2.0"


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sql_int(value):
    return value if -(1 << 63) <= value < (1 << 63) else str(value)


def point_number_starts(rows):
    """Assign report-wide ranges in summary instance order and fixed metric order."""
    starts, next_number = {}, 1
    for row in rows:
        for metric in METRICS:
            starts[row["instance_id"], metric] = next_number
            next_number += row["metrics"][metric]["uncovered_count"]
    return starts


def point_label(number):
    return f"UC-{number:06d}"


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
            CREATE TABLE instances (id INTEGER PRIMARY KEY, path TEXT UNIQUE, selected INTEGER, module_name TEXT);
            CREATE TABLE models (id INTEGER PRIMARY KEY, group_key TEXT UNIQUE, metric TEXT, info TEXT);
            CREATE TABLE scopes (model INTEGER, instance INTEGER, covered, coverable, atomic_targets INTEGER,
                PRIMARY KEY(model,instance)) WITHOUT ROWID;
            CREATE TABLE sources (id INTEGER PRIMARY KEY, path TEXT, mapping TEXT);
            CREATE TABLE targets (id INTEGER PRIMARY KEY, model INTEGER, ordinal INTEGER, source INTEGER,
                line INTEGER, locator BLOB, trail TEXT, representative INTEGER, selected INTEGER,
                description TEXT, sort_key TEXT);
            CREATE UNIQUE INDEX target_identity ON targets(model,source,locator);
            CREATE UNIQUE INDEX reference_order ON targets(model,ordinal);
            CREATE INDEX target_order ON targets(model,selected,id);
            CREATE TABLE states (instance INTEGER, target INTEGER, covered, coverable, raw, status TEXT,
                PRIMARY KEY(instance,target)) WITHOUT ROWID;
            CREATE INDEX instance_status ON states(instance,status,target);
        """)
        self.batch_size = min(max(1, batch_size), 500)
        self.next_target = 0
        self.source_ids = {}
        self.metrics = {}
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
                                     packed(trail), representative, int(selected),
                                     target_description(self.metrics[model], trail), locator_sort(trail)))
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
        self.pending_states.append((instance, target, *(sql_int(v) for v in facts), display_status(*facts)))
        self.stats["native_atomic_state_reads"] += 1
        if len(self.pending_states) >= self.batch_size:
            self.flush()

    def flush(self, commit=False):
        if self.pending_targets:
            self.db.executemany("INSERT INTO targets VALUES (?,?,?,?,?,?,?,?,?,?,?)", self.pending_targets)
            self.pending_targets.clear()
        if self.pending_selected:
            self.db.executemany("UPDATE targets SET selected=1 WHERE id=?", self.pending_selected)
            self.pending_selected.clear()
        if self.pending_states:
            self.db.executemany("INSERT INTO states VALUES (?,?,?,?,?,?)", self.pending_states)
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


class FactsReader:
    """Read original facts without NPI, including legacy facts version 1.0."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self.db.execute("PRAGMA cache_size=-16384")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.create_function("display_status", 3, lru_cache(4096)(
            lambda c, t, r: display_status(int(c), int(t), int(r))), deterministic=True)
        self.db.create_function("locator_sort", 1, lambda trail: locator_sort(json.loads(trail)), deterministic=True)
        try:
            self.metadata = {k: json.loads(v) for k, v in self.db.execute("SELECT key,value FROM metadata")}
            self.version = self.metadata.get("schema_version")
            require(self.version in ("1.0", FACTS_VERSION), f"Unsupported facts version: {self.version}")
            for key in ("database_id", "test_scope_ref", "configuration"):
                require(key in self.metadata, f"Facts metadata missing: {key}")
            self.instances = dict(self.db.execute("SELECT id,path FROM instances WHERE selected=1"))
            self.models = {mid: (metric, json.loads(info))
                           for mid, metric, info in self.db.execute("SELECT id,metric,info FROM models")}
            self.sources = {sid: (path, json.loads(mapping))
                            for sid, path, mapping in self.db.execute("SELECT id,path,mapping FROM sources")}
        except BaseException:
            self.close()
            raise

    def close(self):
        self.db.close()

    @property
    def status_sql(self):
        return "s.status" if self.version == FACTS_VERSION else "display_status(s.covered,s.coverable,s.raw)"

    def summaries(self):
        requested = self.metadata["configuration"].get("metrics")
        names = dict(self.db.execute("SELECT id,module_name FROM instances")) if self.version == FACTS_VERSION else {}
        rows = {iid: {"instance_id": iid, "instance_path": path, "module_name": names.get(iid), "metrics": {
            metric: {"status": "not_selected" if requested and metric not in requested else "not_collected_or_unavailable",
                     "uncovered_count": 0, "atomic_count": 0, "states": {}, "pages": []}
            for metric in METRICS}} for iid, path in self.instances.items()}
        for mid, iid, covered, total, _ in self.db.execute("SELECT * FROM scopes"):
            if iid not in rows:
                continue
            metric, info = self.models[mid]
            row = rows[iid]
            row["module_name"] = row["module_name"] or info.get("module_name")
            row["metrics"][metric].update(status="no_atomic_points", native_covered=int(covered), native_coverable=int(total))
        query = ("SELECT s.instance,m.metric," + self.status_sql + ",COUNT(*) FROM states s "
                 "JOIN targets t ON t.id=s.target JOIN models m ON m.id=t.model "
                 "JOIN instances i ON i.id=s.instance WHERE i.selected=1 GROUP BY s.instance,m.metric,3")
        for iid, metric, status, count in self.db.execute(query):
            if status == "excluded":
                continue
            item = rows[iid]["metrics"][metric]
            item["states"][status] = count
            item["atomic_count"] += count
            item["uncovered_count"] += count if status == "uncovered" else 0
            item["status"] = "analyzed"
        return sorted(rows.values(), key=lambda row: (natural_key(row["instance_path"]), row["instance_path"]))

    def holes(self, iid, metric, ordered=True):
        fields = ("t.description,t.sort_key" if self.version == FACTS_VERSION
                  else "NULL,locator_sort(t.trail)")
        query = ("SELECT t.id,t.source,t.line,t.trail,s.covered,s.coverable,s.raw," + fields +
                 " FROM states s JOIN targets t ON t.id=s.target JOIN models m ON m.id=t.model JOIN sources src ON src.id=t.source "
                 "WHERE s.instance=? AND m.metric=? AND " + self.status_sql + "='uncovered'")
        if ordered:
            query += " ORDER BY src.path,t.line,9,t.id"
        for tid, sid, line, trail, covered, total, raw, description, _ in self.db.execute(query, (iid, metric)):
            yield {"id": tid, "source": sid, "line": line, "trail": trail, "covered": int(covered),
                   "coverable": int(total), "raw": int(raw), "description": description}

    def numbered_holes(self, iid, metric, first_point_no):
        for number, point in enumerate(self.holes(iid, metric), first_point_no):
            yield {**point, "point_no": number}

    def details(self, kind="holes"):
        require(kind == "holes", "Difference exports were removed; use --kind holes for instance uncovered points")
        rows = self.summaries()
        starts = point_number_starts(rows)
        for row in rows:
            iid, path = row["instance_id"], row["instance_path"]
            for metric in METRICS:
                if not row["metrics"][metric]["uncovered_count"]:
                    continue
                model = self.db.execute("SELECT m.info FROM scopes sc JOIN models m ON m.id=sc.model "
                                        "WHERE sc.instance=? AND m.metric=? LIMIT 1", (iid, metric)).fetchone()
                model_id = json.loads(model[0]).get("model_key") if model else None
                for point in self.numbered_holes(iid, metric, starts[iid, metric]):
                    trail = json.loads(point["trail"])
                    source, mapping = self.sources[point["source"]]
                    target = {"bin_name": trail[-1]["name"] if trail else None, "objects": trail,
                              "description": " / ".join(str(step["name"]) for step in trail)}
                    if metric == "tgl":
                        target.update(direction=target["bin_name"], signal=next((step["name"] for step in reversed(trail[:-1])
                            if step["type"] in ("npiCovSignalBit", "npiCovSignal")), None))
                    yield {"point_no": point["point_no"],
                           "target_id": stable_id([self.metadata["database_id"], self.metadata["test_scope_ref"], path, metric, trail]),
                           "database_id": self.metadata["database_id"], "test_scope_ref": self.metadata["test_scope_ref"],
                           "instance_path": path, "module_name": row["module_name"], "model_id": model_id, "metric": metric,
                           "scope_kind": "instance_self", "source_file": source, "source_mapping": mapping,
                           "start_line": point["line"], "end_line": point["line"], "native_locator": trail,
                           "target": target, **state_from_facts(point["covered"], point["coverable"], point["raw"])}


def upgrade_copy(path):
    """Upgrade only a newly copied legacy DB; the source result stays read-only."""
    db = sqlite3.connect(path)
    try:
        metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
        require(metadata.get("schema_version") == "1.0", "Only facts 1.0 need migration")
        db.create_function("display_status", 3, lru_cache(4096)(
            lambda c, t, r: display_status(int(c), int(t), int(r))))
        db.create_function("locator_sort", 1, lambda trail: locator_sort(json.loads(trail)))
        db.create_function("description", 2, lambda metric, trail: target_description(metric, json.loads(trail)))
        db.executescript("""
            ALTER TABLE instances ADD COLUMN module_name TEXT;
            ALTER TABLE states ADD COLUMN status TEXT;
            UPDATE states SET status=display_status(covered,coverable,raw);
            CREATE INDEX instance_status ON states(instance,status,target);
            ALTER TABLE targets ADD COLUMN description TEXT;
            ALTER TABLE targets ADD COLUMN sort_key TEXT;
            UPDATE targets SET sort_key=locator_sort(trail),
                description=description((SELECT metric FROM models WHERE id=targets.model),trail);
            DROP TABLE IF EXISTS classifications;
            DROP TABLE IF EXISTS patterns;
            DROP TABLE IF EXISTS findings;
            DELETE FROM metadata WHERE key='comparison_basis';
        """)
        db.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (packed(FACTS_VERSION),))
        db.commit()
    finally:
        db.close()

