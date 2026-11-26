"""Read each selected instance's own coverage facts without peer analysis."""
from collections import Counter
import shutil
import time

from .common import METRICS, display_status, in_scope, is_excluded, location, native_totals, require, stable_id
from .facts import FACTS_VERSION, FactStore, packed, sql_int


def analyze(session, config, stage, work, mapper, database_id, timings):
    store = FactStore(work / "coverage_facts.sqlite", config.get("batch_size", 256))
    try:
        db = store.db
        paths = sorted(path for path in session.instances if in_scope(path, config["instance_roots"]))
        metadata = {"schema_version": FACTS_VERSION, "database_id": database_id,
                    "test_scope_ref": stable_id(session.selected_tests), "selected_tests": session.selected_tests,
                    "configuration": config}
        db.executemany("INSERT INTO metadata VALUES (?,?)", ((key, packed(value)) for key, value in metadata.items()))
        requested = config["metrics"] or list(METRICS)
        infos = {(path, metric): session.info(path, metric) for path in paths for metric in requested}
        instance_ids = {path: iid for iid, path in enumerate(paths, 1)}
        db.executemany("INSERT INTO instances VALUES (?,?,1,?)", ((iid, path, infos[path, requested[0]].get("module_name"))
                                                               for path, iid in instance_ids.items()))
        summary, capabilities, models = {}, {}, {}
        tick, last_progress = time.perf_counter(), time.monotonic()
        for metric in requested:
            scopes = []
            enumerated = 0
            for path in paths:
                handle = session.handle(path, metric)
                if handle is None:
                    continue
                info = infos[path, metric]
                identity = ("model", info["model_key"]) if info.get("model_key") else ("instance", path)
                key = stable_id([metric, identity])
                if key not in models:
                    mid = len(models) + 1
                    models[key] = mid
                    store.metrics[mid] = metric
                    db.execute("INSERT INTO models VALUES (?,?,?,?)", (mid, key, metric, packed(info)))
                mid, iid = models[key], instance_ids[path]
                first = db.execute("SELECT 1 FROM scopes WHERE model=? LIMIT 1", (mid,)).fetchone() is None
                reference = None if first else iter(store.reference(mid))
                covered, total = native_totals(handle, session.test, session.cov.release_handle)
                require(0 <= covered <= total, f"Invalid native totals for {path}/{metric}: {covered}/{total}")
                counts = Counter()
                for ordinal, (node, trail, parents) in enumerate(session.bins(path, metric)):
                    enumerated += 1
                    source, line = location(parents, trail)
                    sid = store.source(source, mapper)
                    expected = next(reference, None) if reference is not None else None
                    if reference is None:
                        target = store.add_target(mid, ordinal, sid, line, trail, iid, True)
                    elif expected is not None and expected[1] == sid and expected[2] == list(trail):
                        target = expected[0]
                        store.stats["verified_descriptor_reuses"] += 1
                    else:
                        target = store.fallback_target(mid, sid, line, trail, iid, True)
                    facts = node.covered(session.test), node.coverable(session.test), node.status(session.test)
                    store.add_state(iid, target, facts)
                    if not is_excluded(facts[2]):
                        counts[display_status(*facts)] += 1
                    if (ordinal + 1) % (store.batch_size * 32) == 0:
                        store.flush(commit=True)
                    if time.monotonic() - last_progress >= 5:
                        print(f"coverage: scanning {path}/{metric}, {store.stats['native_atomic_state_reads']} states", flush=True)
                        last_progress = time.monotonic()
                store.flush()
                db.execute("INSERT INTO scopes VALUES (?,?,?,?,?)", (mid, iid, sql_int(covered), sql_int(total), sum(counts.values())))
                scopes.append({"instance_path": path, "covered": covered, "coverable": total, "counts": dict(counts)})
                store.flush(commit=True)
            shape = (session.vdb / f"snps/coverage/db/shape/{metric}.verilog.shape.xml").exists()
            available = shape or enumerated > 0
            capabilities[metric] = {"status": "available" if available else "not_collected_or_unavailable",
                                    "native_scopes": sum(bool(scope["counts"]) for scope in scopes), "native_reader": "Verdi NPI"}
            require(available or not config["metrics"], f"Requested metric {metric} is not collected/readable in the selected instance scope")
            if available:
                states = Counter()
                for scope in scopes:
                    states.update(scope["counts"])
                summary[metric] = {"native_covered": sum(scope["covered"] for scope in scopes),
                    "native_coverable": sum(scope["coverable"] for scope in scopes),
                    "uncovered_candidates": states["uncovered"], "output_count": states["uncovered"],
                    "visited_atomic_bins": sum(states.values()), "states": dict(states), "scope_count": len(scopes),
                    "scopes": scopes, "count_unit": "instance_atomic_point"}
        require(summary, "No implemented Verilog/SystemVerilog metrics are available in this VDB/scope")
        timings["fact_scan_seconds"] = time.perf_counter() - tick
        store.discard_excluded()
        store.flush(commit=True)
        stats = dict(store.stats)
    finally:
        store.close()
    tick = time.perf_counter()
    shutil.copy2(store.path, stage / "coverage_facts.sqlite")
    timings["facts_publish_seconds"] = time.perf_counter() - tick
    return summary, capabilities, stats
