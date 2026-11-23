"""Bounded, session-local comparison of descriptor-checked native targets."""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .common import METRICS, ToolError, display_status, read_targets, stable_id, state_from_facts


def compact_state(value):
    return value if "error" in value else (value["covered"], value["coverable"], value["raw_status"])


def expand_state(value):
    return dict(value) if isinstance(value, dict) else state_from_facts(*value)


class SnapshotCache:
    """Small memory LRU backed by SQLite; never retains native handles."""
    def __init__(self, db=None, capacity=1024, path=None, batch_size=256):
        if db is not None and path is not None:
            raise ValueError("Supply a shared connection or an owned cache path")
        self.owned = db is None
        self.path = Path(path) if path is not None else None
        # Never open/truncate a caller's existing file as an owned scratch cache.
        if self.path is not None:
            with self.path.open("xb"):
                pass
        self.db = sqlite3.connect(str(self.path) if self.path else "") if self.owned else db
        # A bounded pager cache avoids one NFS read for each small state lookup.
        # This changes memory budgeting, not SQLite journaling or durability.
        self.db.execute("PRAGMA cache_size=-16384")
        self.namespace = uuid.uuid4().hex
        self.capacity = max(0, capacity)
        self.batch_size = max(1, batch_size)
        self.memory = OrderedDict()
        self.pending_states = {}
        self.uncommitted_states = 0
        self.closed = False
        self.db.execute("CREATE TABLE IF NOT EXISTS peer_cache (namespace TEXT, kind TEXT, key TEXT, data TEXT, "
                        "PRIMARY KEY(namespace,kind,key)) WITHOUT ROWID")
        self.db.execute("CREATE TABLE IF NOT EXISTS peer_states (namespace TEXT, instance INTEGER, metric INTEGER, "
                        "locator BLOB, covered, coverable, raw, error TEXT, "
                        "PRIMARY KEY(namespace,instance,metric,locator)) WITHOUT ROWID")
        # Bounded exact membership avoids result lookups for fresh all targets.
        # If selected exceeds the bound, fall back to batched SQL membership.
        self.result_keys = set()
        self.counts = Counter()

    def _retain(self, key, value):
        if not self.capacity:
            return
        self.memory[key] = value
        self.memory.move_to_end(key)
        if len(self.memory) > self.capacity:
            self.memory.popitem(last=False)

    def get(self, kind, key):
        identity = kind, key
        if identity in self.memory:
            self.memory.move_to_end(identity)
            self.counts["memory_hits"] += 1
            return self.memory[identity]
        row = self.db.execute("SELECT data FROM peer_cache WHERE namespace=? AND kind=? AND key=?",
                              (self.namespace, kind, key)).fetchone()
        if row is None:
            self.counts["misses"] += 1
            return None
        self.counts["disk_hits"] += 1
        value = json.loads(row[0])
        self._retain(identity, value)
        return value

    def put(self, kind, key, value):
        self.db.execute("INSERT OR REPLACE INTO peer_cache VALUES (?,?,?,?)",
                        (self.namespace, kind, key, json.dumps(value, separators=(",", ":"))))
        self._retain((kind, key), value)
        if kind == "result" and self.result_keys is not None:
            self.result_keys.add(key)
            if len(self.result_keys) > max(self.capacity, self.batch_size):
                self.result_keys = None

    def existing_results(self, keys):
        if self.result_keys is not None:
            keys = (key for key in keys if key in self.result_keys)
        return self.get_many("result", keys)

    @staticmethod
    def _sql_int(value):
        # SQLite integers are signed 64-bit; retain larger native values exactly.
        return value if -(1 << 63) <= value < (1 << 63) else str(value)

    @staticmethod
    def _decode_state(row):
        return {"error": row[3]} if row[3] is not None else tuple(int(v) for v in row[:3])

    def get_states(self, keys):
        values, missing = {}, defaultdict(list)
        for key in dict.fromkeys(keys):
            identity = "fact", key
            if key in self.pending_states:
                values[key] = self.pending_states[key]
                self.counts["pending_hits"] += 1
            elif identity in self.memory:
                self.memory.move_to_end(identity)
                values[key] = self.memory[identity]
                self.counts["memory_hits"] += 1
            else:
                missing[key[:2]].append(key[2])
        for (instance, metric), locators in missing.items():
            locators.sort()
            for offset in range(0, len(locators), 500):
                chunk = locators[offset:offset + 500]
                marks = ",".join("?" for _ in chunk)
                self.counts["state_query_batches"] += 1
                rows = self.db.execute(f"SELECT locator,covered,coverable,raw,error FROM peer_states "
                    f"WHERE namespace=? AND instance=? AND metric=? AND locator IN ({marks})",
                    (self.namespace, instance, metric, *chunk))
                for locator, *facts in rows:
                    key = instance, metric, locator
                    value = self._decode_state(facts)
                    values[key] = value
                    self._retain(("fact", key), value)
                    self.counts["disk_hits"] += 1
                self.counts["misses"] += sum((instance, metric, locator) not in values for locator in chunk)
        return values

    def scope_states(self, instance, metric):
        """Bounded exact pre-existing facts before a unique native scope walk.

        A small selected prefix fits here; larger prefixes use normal lookups.
        Facts added by this walk need no existence query: each full indexed
        locator occurs once within the scope. No native handles are retained.
        """
        self.flush()
        self.counts["state_query_batches"] += 1
        rows = self.db.execute("SELECT locator,covered,coverable,raw,error FROM peer_states "
            "WHERE namespace=? AND instance=? AND metric=? LIMIT ?",
            (self.namespace, instance, metric, self.batch_size + 1)).fetchall()
        if len(rows) > self.batch_size:
            return None
        return {(instance, metric, locator): self._decode_state(facts) for locator, *facts in rows}

    def put_states(self, values):
        for key, value in values.items():
            self.pending_states[key] = value
            self._retain(("fact", key), value)
            if len(self.pending_states) >= self.batch_size:
                self.flush()

    def flush(self, commit=False):
        if self.pending_states:
            def rows():
                for (instance, metric, locator), value in self.pending_states.items():
                    facts = (None, None, None, value["error"]) if isinstance(value, dict) else (
                        *(self._sql_int(v) for v in value), None)
                    yield self.namespace, instance, metric, locator, *facts
            self.db.executemany("INSERT OR REPLACE INTO peer_states VALUES (?,?,?,?,?,?,?,?)", rows())
            self.counts["state_insert_batches"] += 1
            self.counts["state_rows_written"] += len(self.pending_states)
            self.uncommitted_states += len(self.pending_states)
            self.pending_states.clear()
        # A shared connection's transaction always belongs to its caller.
        if self.owned and (commit or self.uncommitted_states >= 8192):
            self.db.commit()
            self.uncommitted_states = 0
            self.counts["commits"] += 1

    def get_many(self, kind, keys):
        values, missing = {}, []
        for key in dict.fromkeys(keys):
            identity = kind, key
            if identity in self.memory:
                self.memory.move_to_end(identity)
                self.counts["memory_hits"] += 1
                values[key] = self.memory[identity]
            else:
                missing.append(key)
        # Sorted instance-prefixed keys keep each batch's reads close on disk.
        missing.sort()
        for offset in range(0, len(missing), 500):
            chunk = missing[offset:offset + 500]
            marks = ",".join("?" for _ in chunk)
            found = self.db.execute(f"SELECT key,data FROM peer_cache WHERE namespace=? AND kind=? "
                                    f"AND key IN ({marks}) ORDER BY key", (self.namespace, kind, *chunk))
            for key, data in found:
                value = json.loads(data)
                values[key] = value
                self._retain((kind, key), value)
                self.counts["disk_hits"] += 1
        self.counts["misses"] += len(missing) - sum(key in values for key in missing)
        return values

    def clear(self):
        self.memory.clear()
        self.pending_states.clear()
        self.db.execute("DELETE FROM peer_cache WHERE namespace=?", (self.namespace,))
        self.db.execute("DELETE FROM peer_states WHERE namespace=?", (self.namespace,))
        self.result_keys = set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.memory.clear()
        self.pending_states.clear()
        self.result_keys = set()
        if not self.owned:
            self.clear()
            return
        # Disposable databases are closed and unlinked, never row-deleted.
        try:
            self.db.close()
        finally:
            if self.path is not None:
                for suffix in ("", "-journal", "-wal", "-shm"):
                    self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)


class TargetInventory:
    """Bounded staging of first-seen model targets, preserving their old sort IDs."""
    def __init__(self, db, stats, batch_size=256, identity_capacity=1024):
        self.db, self.stats = db, stats
        self.batch_size = batch_size
        self.identity_capacity = identity_capacity
        self.identities = OrderedDict()
        self.pending = {}
        db.execute("CREATE TABLE comparison_targets (identity TEXT PRIMARY KEY, record TEXT)")

    def identity(self, metric, model, source, trail):
        # Compare every descriptor from the actual traversal, including indices.
        # No native handles or assumptions about checksum-only equality survive.
        signature = metric, model, source, tuple(tuple(sorted(step.items())) for step in trail)
        if signature in self.identities:
            self.identities.move_to_end(signature)
            self.stats["inventory_identity_hits"] += 1
            return self.identities[signature]
        value = stable_id([metric, model, source, list(trail)]), stable_id(trail)
        if self.identity_capacity:
            self.identities[signature] = value
            if len(self.identities) > self.identity_capacity:
                self.identities.popitem(last=False)
        return value

    def add(self, key, record):
        self.stats["inventory_atomic_targets"] += 1
        if key in self.pending:
            self.stats["inventory_duplicate_targets"] += 1
            return
        self.pending[key] = record
        if len(self.pending) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        keys = list(self.pending)
        existing = set()
        for offset in range(0, len(keys), 500):
            chunk = keys[offset:offset + 500]
            marks = ",".join("?" for _ in chunk)
            existing.update(row[0] for row in self.db.execute(
                f"SELECT identity FROM comparison_targets WHERE identity IN ({marks})", chunk))
        self.stats["inventory_duplicate_targets"] += len(existing)
        rows = [(key, json.dumps(record)) for key, record in self.pending.items() if key not in existing]
        if rows:
            self.db.executemany("INSERT INTO comparison_targets VALUES (?,?)", rows)
            self.stats["inventory_records_serialized"] += len(rows)
            self.stats["inventory_insert_batches"] += 1
        self.pending.clear()


class PeerComparison:
    def __init__(self, session, metrics=None, db=None, cache_entries=1024, profile=False,
                 cache_path=None, batch_size=256):
        self.session = session
        self.instance_ids = {path: number for number, path in enumerate(session.instances)}
        self.metric_ids = {metric: number for number, metric in enumerate(METRICS)}
        self.profile = profile
        self.stats = Counter()
        self.references = defaultdict(set)
        self.model_instances = defaultdict(set)
        self.groups = OrderedDict()
        self.closed = False
        self.last_progress = time.perf_counter()
        tick = time.perf_counter()
        for path, instance in session.instances.items():
            sources = session.definitions.get(path, {}).get("sources", [])
            if not sources:
                source = instance.file_name()
                sources = [source] if source else []
            for source in sources:
                self.references[source].add(path)
            for metric in metrics or METRICS:
                key = session.info(path, metric)["model_key"]
                if key is not None:
                    self.model_instances[metric, key].add(path)
        # Indexing can raise a native exception; allocate the owned connection
        # only after it succeeds, so a failed constructor cannot leak a file.
        self.cache = SnapshotCache(db, cache_entries, path=cache_path, batch_size=batch_size)
        self.stats["index_seconds"] = time.perf_counter() - tick

    def state_key(self, path, metric, locator):
        return self.instance_ids[path], self.metric_ids[metric], bytes.fromhex(locator)

    def _identity(self, record):
        path, metric = record["instance_path"], record["metric"]
        model = self.session.info(path, metric)["model_key"]
        # Tag the fallback: an unknown model must never share another instance's result.
        group = metric, record["source_file"], ("model", model) if model is not None else ("instance", path)
        locator = stable_id(record["native_locator"])
        return group, locator, stable_id([group, locator])

    def _group(self, group, record):
        if group in self.groups:
            self.groups.move_to_end(group)
            return self.groups[group]
        path, metric = record["instance_path"], record["metric"]
        model = self.session.info(path, metric)["model_key"]
        paths = sorted(self.references[record["source_file"]] | self.model_instances[metric, model] | {path})
        eligible, incompatible = [], []
        for other in paths:
            if other != path and (model is None or model != self.session.info(other, metric)["model_key"]):
                incompatible.append({"instance_path": other, "reason": "definition/model identity differs or unavailable"})
            else:
                eligible.append(other)
        result = paths, eligible, incompatible
        self.groups[group] = result
        if len(self.groups) > 256:
            self.groups.popitem(last=False)
        return result

    def snapshot_scope(self, path, metric):
        if self.closed:
            raise RuntimeError("Comparison cache was closed before native state mutation")
        return self.cache.scope_states(self.instance_ids[path], self.metric_ids[metric])

    def snapshot(self, path, metric, trail, node, locator=None, existing=None, compact=False):
        """Reuse/seed a state during a separate completed-before-comparison walk."""
        if self.closed:
            raise RuntimeError("Comparison cache was closed before native state mutation")
        locator = stable_id(trail) if locator is None else locator
        key = self.state_key(path, metric, locator)
        value = existing.get(key) if existing is not None else self.cache.get_states([key]).get(key)
        if value is None:
            value = node.covered(self.session.test), node.coverable(self.session.test), node.status(self.session.test)
            self.stats["enumerated_state_reads"] += 1
            self.cache.put_states({key: value})
        else:
            self.stats["enumerated_state_hits"] += 1
        return value if compact else expand_state(value)

    def attach(self, record):
        self.attach_many([record])

    def attach_unique(self, records):
        """All inventory supplies each model target once; expand only emitted rows."""
        return self.attach_many(records, unique=True)

    def attach_many(self, records, *, unique=False):
        if self.closed:
            raise RuntimeError("Comparison cache was closed before native state mutation")
        records = list(records)
        started = time.perf_counter()
        entries, results, pending = [], {}, {}
        identities = [self._identity(record) for record in records]
        result_keys = (key for _, _, key in identities)
        cached_results = self.cache.existing_results(result_keys) if unique else self.cache.get_many("result", result_keys)
        for record, (group, locator, key) in zip(records, identities):
            entries.append((record, key))
            self.stats["input_records"] += 1
            if key in results or key in pending:
                self.stats["comparison_hits"] += 1
                continue
            result = cached_results.get(key)
            if result is not None:
                results[key] = result
                self.stats["comparison_hits"] += 1
            else:
                pending[key] = (record, locator, self._group(group, record))
                self.stats["unique_model_targets"] += 1

        tasks, snapshots = defaultdict(dict), {}
        for record, locator, (_, eligible, _) in pending.values():
            metric = record["metric"]
            for path in eligible:
                key = self.state_key(path, metric, locator)
                if key in snapshots or key in tasks[path, metric]:
                    continue
                tasks[path, metric][key] = record["native_locator"]
        snapshots = self.cache.get_states(key for targets in tasks.values() for key in targets)
        self.stats["state_hits"] += len(snapshots)
        for targets in tasks.values():
            for key in targets.keys() & snapshots.keys():
                del targets[key]
        self.stats["planning_seconds"] += time.perf_counter() - started

        tick = time.perf_counter()
        for (path, metric), targets in tasks.items():
            if not targets:
                continue
            root = self.session.handle(path, metric)
            if root is None:
                values = {key: {"error": "metric unavailable"} for key in targets}
            else:
                self.stats["batch_traversals" if len(targets) > 1 else "single_traversals"] += 1
                def heartbeat():
                    now = time.perf_counter()
                    if now - self.last_progress >= 5:
                        self.last_progress = now
                        print(f"comparison: reading {path}/{metric}, {self.stats['native_state_reads']} instance targets read", flush=True)
                values = read_targets(root, targets, self.session.test, self.session.cov.release_handle,
                                      self.stats if self.profile else None, heartbeat)
                self.stats["native_state_reads"] += sum("error" not in value for value in values.values())
            values = {key: compact_state(value) for key, value in values.items()}
            snapshots.update(values)
            self.cache.put_states(values)
        self.stats["native_read_and_cache_seconds"] += time.perf_counter() - tick

        tick = time.perf_counter()
        for key, (record, locator, (paths, eligible, incompatible)) in pending.items():
            facts, errors, statuses = [], {}, set()
            incompatible = list(incompatible)
            for path in eligible:
                value = snapshots[self.state_key(path, record["metric"], locator)]
                if isinstance(value, dict):
                    errors[path] = value["error"]
                    incompatible.append({"instance_path": path, "reason": value["error"]})
                else:
                    facts.append((path, value))
                    statuses.add(display_status(*value))
            if record["instance_path"] in errors:
                raise ToolError(errors[record["instance_path"]])
            if unique and len(statuses) <= 1 and not incompatible:
                results[key] = None
                self.stats["all_results_skipped"] += 1
                continue
            peers = [{"instance_path": path, **expand_state(value)} for path, value in facts]
            incompatible.sort(key=lambda row: row["instance_path"])
            result = {"errors": errors, "coverage": {
                "comparison_basis": "original source + identical definition and metric checksums + native locator descriptors",
                "instance_count": len(paths), "comparable_instance_count": len(peers), "instances": peers,
                "incomparable_instances": incompatible,
                "same_target_differs": len(statuses) > 1}}
            if not unique:
                self.cache.put("result", key, result)
            else:
                self.stats["all_results_expanded"] += 1
            results[key] = result
        emitted = []
        for record, key in entries:
            result = results[key]
            if result is None:
                continue
            if record["instance_path"] in result["errors"]:
                raise ToolError(result["errors"][record["instance_path"]])
            coverage = result["coverage"]
            if unique and not (coverage["same_target_differs"] or coverage["incomparable_instances"]):
                self.stats["all_results_skipped"] += 1
                continue
            record["same_rtl_coverage"] = coverage if unique and key in pending else deepcopy(coverage)
            emitted.append(record)
        self.stats["assembly_seconds"] += time.perf_counter() - tick
        return emitted

    def report(self):
        return {**dict(self.stats), "cache": dict(self.cache.counts),
                "memory_cache_entries": len(self.cache.memory), "memory_cache_capacity": self.cache.capacity,
                "snapshot_batch_size": self.cache.batch_size, "snapshot_pending_entries": len(self.cache.pending_states),
                "cache_storage": "owned_database" if self.cache.owned else "shared_namespace",
                "locator_strategy": {"single_target_max": 1, "multiple_targets": "shared_prefix_trie"},
                "profile_native_calls": self.profile}

    def close(self):
        if not self.closed:
            self.closed = True
            self.groups.clear()
            self.references.clear()
            self.model_instances.clear()
            self.cache.close()
