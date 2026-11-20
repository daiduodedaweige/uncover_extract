"""Bounded, session-local comparison of descriptor-checked native targets."""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
import json
import sqlite3
import time
import uuid

from .common import METRICS, ToolError, read_targets, stable_id, state


class SnapshotCache:
    """Small memory LRU backed by SQLite; never retains native handles."""
    def __init__(self, db=None, capacity=1024):
        self.owned = db is None
        self.db = sqlite3.connect("") if self.owned else db
        # A bounded pager cache avoids one NFS read for each small state lookup.
        # This changes memory budgeting, not SQLite journaling or durability.
        self.db.execute("PRAGMA cache_size=-16384")
        self.namespace = uuid.uuid4().hex
        self.capacity = max(0, capacity)
        self.memory = OrderedDict()
        self.db.execute("CREATE TABLE IF NOT EXISTS peer_cache (namespace TEXT, kind TEXT, key TEXT, data TEXT, "
                        "PRIMARY KEY(namespace,kind,key)) WITHOUT ROWID")
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
        self.db.execute("DELETE FROM peer_cache WHERE namespace=?", (self.namespace,))

    def close(self):
        self.clear()
        if self.owned:
            self.db.close()


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
    def __init__(self, session, metrics=None, db=None, cache_entries=1024, profile=False):
        self.session = session
        self.cache = SnapshotCache(db, cache_entries)
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
        self.stats["index_seconds"] = time.perf_counter() - tick

    @staticmethod
    def state_key(path, metric, locator):
        return json.dumps([path, metric, locator], separators=(",", ":"))

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

    def snapshot(self, path, metric, trail, node, locator=None):
        """Reuse/seed a state during a separate completed-before-comparison walk."""
        locator = stable_id(trail) if locator is None else locator
        key = self.state_key(path, metric, locator)
        value = self.cache.get("state", key)
        if value is None:
            value = state(node, self.session.test)
            self.stats["enumerated_state_reads"] += 1
            self.cache.put("state", key, value)
        else:
            self.stats["enumerated_state_hits"] += 1
        return value

    def attach(self, record):
        self.attach_many([record])

    def attach_many(self, records):
        if self.closed:
            raise RuntimeError("Comparison cache was closed before native state mutation")
        records = list(records)
        started = time.perf_counter()
        entries, results, pending = [], {}, {}
        identities = [self._identity(record) for record in records]
        cached_results = self.cache.get_many("result", (key for _, _, key in identities))
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
        snapshots = self.cache.get_many("state", (key for targets in tasks.values() for key in targets))
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
            for key, value in values.items():
                snapshots[key] = value
                self.cache.put("state", key, value)
        self.stats["native_read_and_cache_seconds"] += time.perf_counter() - tick

        tick = time.perf_counter()
        for key, (record, locator, (paths, eligible, incompatible)) in pending.items():
            peers, errors = [], {}
            incompatible = list(incompatible)
            for path in eligible:
                value = snapshots[self.state_key(path, record["metric"], locator)]
                if "error" in value:
                    errors[path] = value["error"]
                    incompatible.append({"instance_path": path, "reason": value["error"]})
                else:
                    peers.append({"instance_path": path, **value})
            incompatible.sort(key=lambda row: row["instance_path"])
            result = {"errors": errors, "coverage": {
                "comparison_basis": "original source + identical definition and metric checksums + native locator descriptors",
                "instance_count": len(paths), "comparable_instance_count": len(peers), "instances": peers,
                "incomparable_instances": incompatible,
                "same_target_differs": len({peer["status"] for peer in peers}) > 1}}
            self.cache.put("result", key, result)
            results[key] = result
        for record, key in entries:
            result = results[key]
            if record["instance_path"] in result["errors"]:
                raise ToolError(result["errors"][record["instance_path"]])
            record["same_rtl_coverage"] = deepcopy(result["coverage"])
        self.stats["assembly_seconds"] += time.perf_counter() - tick

    def report(self):
        return {**dict(self.stats), "cache": dict(self.cache.counts),
                "memory_cache_entries": len(self.cache.memory), "memory_cache_capacity": self.cache.capacity,
                "locator_strategy": {"single_target_max": 1, "multiple_targets": "shared_prefix_trie"},
                "profile_native_calls": self.profile}

    def close(self):
        if not self.closed:
            self.cache.close()
            self.closed = True
