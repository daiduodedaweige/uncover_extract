"""Native subprocess entry: native crashes cannot publish partial results."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import gzip
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
from .output import write_results


def all_comparisons(session, config, stage, summary, database_id, inventory):
    comparison = PeerComparison(session)
    checked, differences = 0, 0
    inventory.db.execute("CREATE TABLE comparison_targets (identity TEXT PRIMARY KEY, record TEXT)")
    # Finish native traversal before resolving peers. NPI can alias handles for
    # the same object; releasing a resolved alias during traversal invalidates
    # the generator's live parent handles on some releases.
    for metric, data in summary.items():
        for scope in data["scopes"]:
            path = scope["instance_path"]
            info = session.info(path, metric)
            for node, trail, parents in session.bins(path, metric):
                source, line = location(parents)
                key = stable_id([metric, info["model_key"] or path, source, list(trail)])
                row = {"metric": metric, "instance_path": path, "source_file": source,
                       "start_line": line, "native_locator": list(trail), **state(node, session.test)}
                inventory.db.execute("INSERT OR IGNORE INTO comparison_targets VALUES (?,?)", (key, json.dumps(row)))
        inventory.db.commit()
    with gzip.open(stage / "same_rtl_comparison.jsonl.gz", "wt", encoding="utf-8") as stream:
        for (text,) in inventory.db.execute("SELECT record FROM comparison_targets ORDER BY identity"):
            row = json.loads(text)
            comparison.attach(row)
            checked += 1
            peers = row["same_rtl_coverage"]
            if peers["same_target_differs"] or peers["incomparable_instances"]:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                differences += int(peers["same_target_differs"])
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
                with NativeSession(config, copied) as session:
                    tick = time.perf_counter()
                    summary, capabilities = scan(session, config, inventory, mapper, before["sha256"])
                    timings["scan_seconds"] = time.perf_counter() - tick
                    peer_comparison = PeerComparison(session)
                    tick = time.perf_counter()
                    for metric, counts in summary.items():
                        output_count = 0
                        for record in inventory.candidates(metric, config["limit"]):
                            if config["compare_peers"] != "none":
                                peer_comparison.attach(record)
                            inventory.db.execute("INSERT INTO selected (metric,record) VALUES (?,?)", (metric, json.dumps(record)))
                            output_count += 1
                        counts["output_count"] = output_count
                    inventory.db.commit()
                    comparison_report = {"mode": config["compare_peers"], "scope": "only output targets"}
                    if config["compare_peers"] == "all":
                        comparison_report = all_comparisons(session, config, stage, summary, before["sha256"], inventory)
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
                                    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
                    "limitations": ["FSM/VHDL and cross-compilation target matching are not implemented",
                                    "Native bin locators are valid only in the recorded model",
                                    "Missing/unmapped RTL never removes a native coverage fact"]}
                write_results(stage, inventory, manifest, config["per_metric"], config["include_other_states"])
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
