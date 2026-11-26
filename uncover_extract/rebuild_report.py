"""Rebuild complete per-instance reports offline into a new result directory."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import sqlite3
import tempfile
import time

from . import SCHEMA_VERSION, __version__
from .common import ToolError, require, write_json
from .facts import FACTS_VERSION, FactsReader, upgrade_copy
from .report import write_report


def rebuild(result, out, page_size=2000, page_bytes=1024 * 1024):
    require(page_size > 0 and page_bytes > 1024, "Page limits must be positive; page bytes must exceed 1024")
    result, out = Path(result).resolve(), Path(out).resolve()
    require(result.is_dir(), "Source result directory does not exist")
    require(not out.exists(), "Choose a new output directory; offline rebuild never replaces an existing result")
    require(result not in out.parents and out not in result.parents, "Source and destination must not overlap")
    # Validate metadata before creating the destination staging directory.
    reader = FactsReader(result / "coverage_facts.sqlite")
    try:
        version = reader.version
    finally:
        reader.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.running-", dir=out.parent))
    start = time.perf_counter()
    before = json.loads((result / "analysis_manifest.json").read_text())
    source = sqlite3.connect((result / "coverage_facts.sqlite").as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(stage / "coverage_facts.sqlite")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    if version != FACTS_VERSION:
        upgrade_copy(stage / "coverage_facts.sqlite")
    copied_seconds = time.perf_counter() - start
    tick = time.perf_counter()
    stats = write_report(stage, {}, page_size, page_bytes)
    summary = json.loads((stage / "instance_summary.json").read_text())
    metrics = {}
    for metric, holes in stats["uncovered_by_metric"].items():
        items = [row["metrics"][metric] for row in summary["instances"]]
        if not any("native_coverable" in item for item in items):
            continue
        metrics[metric] = {"native_covered": sum(item.get("native_covered", 0) for item in items),
                           "native_coverable": sum(item.get("native_coverable", 0) for item in items),
                           "uncovered_candidates": holes, "output_count": holes,
                           "visited_atomic_bins": sum(item["atomic_count"] for item in items),
                           "count_unit": "instance_atomic_point"}
    manifest = {"schema_version": SCHEMA_VERSION, "tool": "uncover-extract", "tool_version": __version__,
        "status": "success", "created_utc": datetime.now(timezone.utc).isoformat(),
        "operation": "offline_report_rebuild", "facts_schema_version": FACTS_VERSION,
        "facts_source": {"result": str(result), "schema_version": version},
        "summary": metrics, "report": stats,
        "performance": {"timings": {"facts_copy_and_upgrade_seconds": copied_seconds,
                        "output_seconds": time.perf_counter() - tick}, "total_seconds": time.perf_counter() - start,
                        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}}
    for key in ("input", "configuration", "selected_tests", "available_tests", "test_scope_ref", "source_mappings", "warnings", "capabilities"):
        if key in before:
            manifest[key] = before[key]
    manifest["outputs"] = sorted(str(path.relative_to(stage)) for path in stage.rglob("*") if path.is_file()) + ["analysis_manifest.json"]
    write_json(stage / "analysis_manifest.json", manifest)
    stage.rename(out)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--page-size", type=int, default=2000)
    parser.add_argument("--page-bytes", type=int, default=1024 * 1024)
    args = parser.parse_args(argv)
    try:
        result = rebuild(args.result, args.out, args.page_size, args.page_bytes)
        print(json.dumps({"out": str(args.out), "report": result["report"]}, ensure_ascii=False))
        return 0
    except (ToolError, OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
