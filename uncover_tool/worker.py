"""Native subprocess entry: native crashes cannot publish partial results."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import time
import traceback

from . import SCHEMA_VERSION, __version__
from .common import fingerprint, require, stable_id, write_json
from .mapping import SourceMapper
from .native import NativeSession
from .differences import analyze, write_report


def run(config, stage):
    started = time.perf_counter()
    timings = {}
    original = Path(config["vdb"])
    before = fingerprint(original)
    timings["fingerprint_before_seconds"] = time.perf_counter() - started
    work = Path(config["scratch_dir"]).resolve()
    try:
        # The parent removes scratch after native log descriptors have closed.
        os.chdir(work)
        copied = work / "input.vdb"
        tick = time.perf_counter()
        shutil.copytree(original, copied)
        require(fingerprint(copied)["sha256"] == before["sha256"], "VDB changed while creating isolated input copy")
        timings["copy_and_verify_seconds"] = time.perf_counter() - tick
        mapper = SourceMapper(config["rtl_roots"], config["path_maps"])
        tick = time.perf_counter()
        with NativeSession(config, copied) as session:
            timings["native_session_seconds"] = time.perf_counter() - tick
            summary, capabilities, comparison_report, comparison_stats = analyze(
                session, config, stage, work, mapper, before["sha256"], timings)
            selected_tests = session.selected_tests
            available_tests = session.available_tests
            warnings = session.warnings
        tick = time.perf_counter()
        require(before == fingerprint(original), "Original VDB content changed during extraction")
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
            "warnings": warnings,
            "performance": {"timings": timings, "seconds_before_output": time.perf_counter() - started,
                            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                            "comparison": comparison_stats},
            "limitations": ["FSM/VHDL and cross-compilation target matching are not implemented",
                            "Native bin locators are valid only in the recorded model",
                            "Missing/unmapped RTL never removes a native coverage fact"]}
        print("output: writing grouped JSON, CSV and report", flush=True)
        tick = time.perf_counter()
        write_report(stage, manifest)
        timings["output_seconds"] = time.perf_counter() - tick
        print(f"output: finished in {timings['output_seconds']:.2f}s", flush=True)
        manifest["performance"]["total_seconds"] = time.perf_counter() - started
        manifest["performance"]["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        manifest["outputs"] = sorted({p.name for p in stage.iterdir() if p.is_file()} | {"analysis_manifest.json"})
        write_json(stage / "analysis_manifest.json", manifest)
        print(json.dumps({"status": "success", "records_per_metric": {m: s["output_count"] for m, s in summary.items()},
                          "seconds": manifest["performance"]["total_seconds"]}), flush=True)
    except BaseException:
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
