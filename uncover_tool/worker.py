"""Native subprocess entry: native crashes cannot publish partial results."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import gzip
from itertools import islice
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import tempfile
import time
import traceback

from . import SCHEMA_VERSION, __version__
from .common import METRICS, fingerprint, require, stable_id, write_json, walk_bins, state, location
from . import exclusions
from .mapping import SourceMapper
from .native import Inventory, NativeSession, PeerComparison, scan
from .comparison import TargetInventory
from .output import write_results


def batches(rows, size):
    rows = iter(rows)
    while batch := list(islice(rows, size)):
        yield batch


class ComparisonProgress:
    def __init__(self, label, total=None):
        self.label, self.total = label, total
        self.started = self.last = time.perf_counter()
        self.update(0, force=True)

    def update(self, done, comparison=None, force=False):
        now = time.perf_counter()
        if not force and now - self.last < 5:
            return
        self.last = now
        elapsed = now - self.started
        count = f"{done}/{self.total}" if self.total is not None else str(done)
        hits = comparison.stats["comparison_hits"] if comparison else 0
        requests = comparison.stats["input_records"] if comparison else 0
        estimate = ""
        if self.total is not None:
            estimate = f", {100 * done / max(self.total, 1):.1f}%"
            if done:
                estimate += f", ETA {max(0, self.total - done) * elapsed / done:.1f}s"
        print(f"comparison: {self.label} {count} targets, {elapsed:.2f}s, "
              f"{done / max(elapsed, 0.001):.1f} targets/s{estimate}, "
              f"{hits} reused comparisons ({100 * hits / max(requests, 1):.1f}%)", flush=True)


def all_comparisons(session, config, stage, summary, database_id, inventory, comparison):
    checked, differences = 0, 0
    targets = TargetInventory(inventory.db, comparison.stats,
        batch_size=config.get("comparison_batch_size", 256),
        identity_capacity=config.get("comparison_cache_entries", 1024))
    progress = ComparisonProgress("inventory all")
    visited = 0
    tick = time.perf_counter()
    # Finish native traversal before resolving peers. NPI can alias handles for
    # the same object; releasing a resolved alias during traversal invalidates
    # the generator's live parent handles on some releases.
    for metric, data in summary.items():
        for scope in data["scopes"]:
            path = scope["instance_path"]
            info = session.info(path, metric)
            for node, trail, parents in session.bins(path, metric):
                source, line = location(parents)
                key, locator = targets.identity(metric, info["model_key"] or path, source, trail)
                row = {"metric": metric, "instance_path": path, "source_file": source,
                       "start_line": line, "native_locator": list(trail),
                       **comparison.snapshot(path, metric, trail, node, locator=locator)}
                targets.add(key, row)
                visited += 1
                progress.update(visited)
        targets.flush()
        inventory.db.commit()
    progress.update(visited, force=True)
    targets.identities.clear()
    comparison.stats["all_inventory_seconds"] = time.perf_counter() - tick
    total = inventory.db.execute("SELECT COUNT(*) FROM comparison_targets").fetchone()[0]
    progress = ComparisonProgress("all", total)
    output_tick = time.perf_counter()
    with gzip.open(stage / "same_rtl_comparison.jsonl.gz", "wt", encoding="utf-8") as stream:
        output_seconds = time.perf_counter() - output_tick
        rows = (json.loads(row[0]) for row in inventory.db.execute("SELECT record FROM comparison_targets ORDER BY identity"))
        for batch in batches(rows, config.get("comparison_batch_size", 256)):
            comparison.attach_many(batch)
            output_tick = time.perf_counter()
            for row in batch:
                checked += 1
                peers = row["same_rtl_coverage"]
                if peers["same_target_differs"] or peers["incomparable_instances"]:
                    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                    differences += int(peers["same_target_differs"])
            output_seconds += time.perf_counter() - output_tick
            inventory.db.commit()
            progress.update(checked, comparison)
        output_tick = time.perf_counter()
    comparison.stats["gzip_output_seconds"] = output_seconds + time.perf_counter() - output_tick
    progress.update(checked, comparison, force=True)
    return {"mode": "all", "unique_model_targets_checked": checked, "targets_with_state_differences": differences,
            "scope": "all atomic targets under requested instance roots; compatible peers may be outside roots"}


def run(config, stage):
    started = time.perf_counter()
    timings = {}
    original = Path(config["vdb"])
    before = fingerprint(original)
    timings["fingerprint_before_seconds"] = time.perf_counter() - started
    work_parent = Path(config["work_dir"]) if config["work_dir"] else stage.parent
    work_parent.mkdir(parents=True, exist_ok=True)
    try:
        # Parent removes this directory after native libraries have closed their NFS log descriptors.
        with nullcontext(config["scratch_dir"]) as directory:
            work = Path(directory).resolve()
            os.chdir(work)
            copied = work / "input.vdb"
            tick = time.perf_counter()
            shutil.copytree(original, copied)
            require(fingerprint(copied)["sha256"] == before["sha256"], "VDB changed while creating isolated input copy")
            timings["copy_and_verify_seconds"] = time.perf_counter() - tick
            inventory = Inventory(work / "inventory.sqlite")
            mapper = SourceMapper(config["rtl_roots"], config["path_maps"])
            try:
                tick = time.perf_counter()
                with NativeSession(config, copied) as session:
                    timings["native_session_seconds"] = time.perf_counter() - tick
                    tick = time.perf_counter()
                    summary, capabilities = scan(session, config, inventory, mapper, before["sha256"])
                    timings["scan_seconds"] = time.perf_counter() - tick
                    tick = time.perf_counter()
                    peer_comparison = None
                    comparison_stats = {"mode": "none"}
                    if config["compare_peers"] != "none":
                        print("comparison: building instance/model index", flush=True)
                        peer_comparison = PeerComparison(session, metrics=summary, db=inventory.db,
                            cache_entries=config.get("comparison_cache_entries", 1024), profile=config.get("profile_comparison", False))
                    for metric, counts in summary.items():
                        output_count = 0
                        total = counts["uncovered_candidates"]
                        if config["limit"] is not None:
                            total = min(total, config["limit"])
                        progress = ComparisonProgress("selected " + metric, total) if peer_comparison else None
                        for batch in batches(inventory.candidates(metric, config["limit"]), config.get("comparison_batch_size", 256)):
                            if peer_comparison:
                                peer_comparison.attach_many(batch)
                            inventory.db.executemany("INSERT INTO selected (metric,record) VALUES (?,?)",
                                ((metric, json.dumps(record)) for record in batch))
                            output_count += len(batch)
                            inventory.db.commit()
                            if progress:
                                progress.update(output_count, peer_comparison)
                        if progress:
                            progress.update(output_count, peer_comparison, force=True)
                        counts["output_count"] = output_count
                    inventory.db.commit()
                    comparison_report = {"mode": config["compare_peers"], "scope": "only output targets"}
                    if config["compare_peers"] == "all":
                        comparison_report = all_comparisons(session, config, stage, summary, before["sha256"], inventory, peer_comparison)
                        timings["comparison_output_seconds"] = peer_comparison.stats["gzip_output_seconds"]
                    if peer_comparison:
                        comparison_stats = peer_comparison.report()
                        # Exclusion export mutates native states. No cached facts survive it.
                        cleanup_started = time.perf_counter()
                        print("comparison: clearing session cache", flush=True)
                        peer_comparison.close()
                        inventory.db.commit()
                        timings["comparison_cache_cleanup_seconds"] = time.perf_counter() - cleanup_started
                    timings["selection_and_comparison_seconds"] = time.perf_counter() - tick
                    validation = {"status": "not_requested"}
                    export_result = {"status": "not_requested"}
                    tick = time.perf_counter()
                    if config["export_exclusions"] or config["validate_exclusions"]:
                        candidate_path = work / "candidate.el"
                        states_before, expected, export_result = exclusions.export(session, inventory, candidate_path, work)
                        if config["export_exclusions"]:
                            shutil.copy2(candidate_path, stage / "extracted_coverage.el")
                    timings["exclusion_export_seconds"] = time.perf_counter() - tick
                    selected_tests = session.selected_tests
                    available_tests = session.available_tests
                    warnings = session.warnings
                if config["validate_exclusions"]:
                    tick = time.perf_counter()
                    # Opening a different copy prevents native cache reuse of mutated objects.
                    validation_copy = work / "validation.vdb"
                    shutil.copytree(original, validation_copy)
                    require(fingerprint(validation_copy)["sha256"] == before["sha256"], "Original VDB changed before validation")
                    with NativeSession(config, validation_copy) as fresh:
                        validation = exclusions.verify(fresh, candidate_path, states_before, expected)
                    timings["exclusion_validation_seconds"] = time.perf_counter() - tick
                    write_json(stage / "coverage_validation.json", validation)
                tick = time.perf_counter()
                after = fingerprint(original)
                require(before == after, "Original VDB content changed during extraction")
                timings["fingerprint_after_seconds"] = time.perf_counter() - tick
                version_path = original / ".vdb_version"
                manifest = {"schema_version": SCHEMA_VERSION, "tool": "uncover-extract", "tool_version": __version__,
                    "status": "success", "created_utc": datetime.now(timezone.utc).isoformat(),
                    "input": {"path": str(original), "version": version_path.read_text().strip() if version_path.exists() else None,
                              "fingerprint": before, "original_unchanged": True},
                    "environment": {"python": sys.version, "verdi_home": config["verdi_home"], "reader": "Verdi NPI"},
                    "configuration": config, "available_tests": available_tests, "selected_tests": selected_tests,
                    "test_scope_ref": stable_id(selected_tests), "capabilities": capabilities, "summary": summary,
                    "same_rtl_comparison": comparison_report, "source_mappings": mapper.report(),
                    "source_correspondence": "unconfirmed; path resolution and current file hashes are not compilation equivalence proof",
                    "exclusion_export": export_result, "exclusion_validation": validation, "warnings": warnings,
                    "performance": {"timings": timings, "seconds_before_output": time.perf_counter() - started,
                                    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                    "comparison": comparison_stats},
                    "limitations": ["FSM/VHDL and cross-compilation target matching are not implemented",
                                    "Native bin locators are valid only in the recorded model",
                                    "Missing/unmapped RTL never removes a native coverage fact"]}
                print("output: writing JSON, CSV and report", flush=True)
                tick = time.perf_counter()
                write_results(stage, inventory, manifest, config["per_metric"], config["include_other_states"])
                timings["output_seconds"] = time.perf_counter() - tick
                timings["output_total_seconds"] = timings["output_seconds"] + timings.get("comparison_output_seconds", 0)
                print(f"output: finished in {timings['output_seconds']:.2f}s", flush=True)
                manifest["performance"]["total_seconds"] = time.perf_counter() - started
                manifest["performance"]["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                write_json(stage / "analysis_manifest.json", manifest)
                print(json.dumps({"status": "success", "records_per_metric": {m: s["output_count"] for m, s in summary.items()},
                                  "seconds": manifest["performance"]["total_seconds"]}), flush=True)
            finally:
                inventory.db.close()
    except BaseException:
        # Failure also audits the input. Never mark a failed run successful.
        unchanged = fingerprint(original) == before
        write_json(stage / "input_audit.json", {"original_unchanged": unchanged, "fingerprint_before": before["sha256"]})
        raise


def main():
    config_path, output = sys.argv[1:]
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    config = json.loads(Path(config_path).read_text())
    stage = Path(output).resolve()
    try:
        run(config, stage)
    except BaseException as exc:
        write_json(stage / "error.json", {"status": "failed", "error": str(exc), "type": type(exc).__name__})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
