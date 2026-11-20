#!/usr/bin/env python3
"""Alternate two source trees on identical native inputs and verify every result."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
from itertools import zip_longest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import digest_file, fingerprint, require, write_json


def source_hash(root):
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted((root / "uncover_tool").glob("*.py")))).hexdigest()


def stream_records(path):
    """Decode the emitted records array without loading millions of facts."""
    with path.open(encoding="utf-8") as stream:
        # json_rows emits one complete JSON record per physical line.
        require(stream.readline().rstrip().endswith('"records":['), f"Invalid records header: {path}")
        for line in stream:
            line = line.strip()
            if line == ']}':
                return
            if not line:
                continue
            yield json.loads(line.removesuffix(','))
        raise ValueError(f"Incomplete records array: {path}")


def equivalent(left, right):
    a, b = (json.loads((p / "analysis_manifest.json").read_text()) for p in (left, right))
    for key in ("input", "selected_tests", "available_tests", "test_scope_ref", "capabilities", "summary",
                "same_rtl_comparison", "source_mappings", "exclusion_export", "exclusion_validation", "warnings"):
        require(a[key] == b[key], f"Manifest differs: {key}: {left} / {right}")
    missing = object()
    with (left / "uncovered_coverage.json").open() as first, (right / "uncovered_coverage.json").open() as second:
        require(first.readline() == second.readline(), "JSON schema header differs")
    for first, second in zip_longest(stream_records(left / "uncovered_coverage.json"),
                                    stream_records(right / "uncovered_coverage.json"), fillvalue=missing):
        require(first == second, "Atomic records or peer results differ")
    for name in ("uncovered_coverage.csv", "report.md"):
        require(digest_file(left / name) == digest_file(right / name), f"Output differs: {name}")
    for name in ("same_rtl_comparison.jsonl.gz", "other_states.jsonl.gz"):
        require((left / name).exists() == (right / name).exists(), f"Output presence differs: {name}")
        if (left / name).exists():
            with gzip.open(left / name, "rt") as first, gzip.open(right / name, "rt") as second:
                for a, b in zip_longest(first, second, fillvalue=missing):
                    require(a is not missing and b is not missing and json.loads(a) == json.loads(b),
                            f"Output differs: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--optimized-dir", type=Path, default=ROOT)
    parser.add_argument("--vdb", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rtl-root", type=Path, action="append", default=[])
    parser.add_argument("--instance-root", action="append", default=[])
    parser.add_argument("--model-peers-of", help="include instances with the same XML identity as this instance")
    parser.add_argument("--tests", nargs="+")
    parser.add_argument("--all-tests", action="store_true")
    parser.add_argument("--metrics", nargs="+", default=["line", "tgl", "branch", "cond"])
    parser.add_argument("--cases", nargs="+", choices=["none", "limited10", "limited100", "selected", "all"],
                        default=["none", "limited10", "limited100", "selected", "all"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--all-output-limit", type=int,
                        help="limit selected main records in all mode; still compare every scoped atomic target")
    parser.add_argument("--profile-optimized", action="store_true", help="count optimized native handle calls in a separate diagnostic run")
    args = parser.parse_args()
    require(args.repeats > 0, "--repeats must be positive")
    require(args.all_output_limit is None or args.all_output_limit > 0, "--all-output-limit must be positive")
    out = args.out.resolve()
    require(not out.exists(), "Choose a fresh benchmark output directory")
    out.mkdir(parents=True)
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    sources = {"baseline": args.baseline_dir.resolve(), "optimized": args.optimized_dir.resolve()}
    before = fingerprint(args.vdb.resolve())
    if args.model_peers_of:
        from uncover_tool.native import xml_hints
        definitions, models, warnings = xml_hints(args.vdb.resolve())
        require(not warnings and args.model_peers_of in definitions, "Model identity unavailable for benchmark selection")
        def identity(path):
            hint = definitions[path]
            return (hint["definition_id"], hint["definition_checksum"],
                    tuple(models.get(metric, {}).get(path) for metric in args.metrics))
        selected_identity = identity(args.model_peers_of)
        args.instance_root += sorted(path for path in definitions if identity(path) == selected_identity)
    common = ["--vdb", str(args.vdb.resolve()), "--metrics", *args.metrics,
              "--work-dir", str(args.work_dir.resolve()), "--timeout", str(args.timeout)]
    for root in args.rtl_root:
        common += ["--rtl-root", str(root.resolve())]
    for root in args.instance_root:
        common += ["--instance-root", root]
    if args.tests:
        common += ["--tests", *args.tests]
    if args.all_tests:
        common += ["--all-tests"]
    options = {"none": ["--compare-peers", "none"], "selected": ["--compare-peers", "selected"],
               "all": ["--compare-peers", "all"],
               "limited10": ["--compare-peers", "selected", "--limit-per-metric", "10"],
               "limited100": ["--compare-peers", "selected", "--limit-per-metric", "100"]}
    if args.all_output_limit is not None:
        options["all"] += ["--limit-per-metric", str(args.all_output_limit)]
    evidence = {"vdb": str(args.vdb.resolve()), "fingerprint": before, "runs": [], "cases": {},
                "source_dirs": {k: str(v) for k, v in sources.items()},
                "source_hashes": {k: source_hash(v) for k, v in sources.items()},
                "python": sys.version, "load_average_before": os.getloadavg(),
                "cache_conditions": "OS caches not flushed; versions alternate; includes ordinary shared-machine load",
                "working_directory": str(args.work_dir.resolve())}
    try:
        for case in args.cases:
            for repeat in range(args.repeats):
                versions = ["baseline", "optimized"] if repeat % 2 == 0 else ["optimized", "baseline"]
                outputs = {}
                for version in versions:
                    destination = out / f"{case}-{repeat}-{version}"
                    command = [sys.executable, str(sources[version] / "uncover_extract.py"), *common,
                               *options[case], "--out", str(destination)]
                    if args.profile_optimized and version == "optimized":
                        command.append("--profile-comparison")
                    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
                    previous_workspaces = set(work.glob(".uncover-work-*"))
                    workspace_bytes = 0
                    tick = time.perf_counter()
                    with (out / f"{case}-{repeat}-{version}.log").open("w") as stream:
                        run = subprocess.Popen(command, cwd=sources[version], env=env, stdout=stream, stderr=subprocess.STDOUT)
                        while True:
                            try:
                                run.wait(timeout=0.25)
                                break
                            except subprocess.TimeoutExpired:
                                for directory in set(work.glob(".uncover-work-*")) - previous_workspaces:
                                    sizes = []
                                    for parent, _, names in os.walk(directory):
                                        for name in names:
                                            try:
                                                sizes.append((Path(parent) / name).stat().st_size)
                                            except FileNotFoundError:
                                                pass  # Worker may remove scratch during sampling.
                                    workspace_bytes = max(workspace_bytes, sum(sizes))
                                if time.perf_counter() - tick > args.timeout + 30:
                                    run.send_signal(signal.SIGINT)
                                    run.wait(timeout=15)
                                    raise RuntimeError("Benchmark parent exceeded timeout")
                    require(run.returncode == 0, f"Benchmark failed: {version}/{case}/{repeat}; inspect log")
                    manifest = json.loads((destination / "analysis_manifest.json").read_text())
                    stats = manifest["performance"]
                    evidence["runs"].append({"case": case, "repeat": repeat, "version": version, "command": command,
                        "wall_seconds": time.perf_counter() - tick, "performance": stats,
                        "sampled_peak_workspace_bytes": workspace_bytes,
                        "output_bytes": sum(p.stat().st_size for p in destination.rglob("*") if p.is_file()),
                        "output_records": {m: s["output_count"] for m, s in manifest["summary"].items()}})
                    outputs[version] = destination
                    print(f"{case} {repeat + 1}/{args.repeats} {version}: comparison "
                          f"{stats['timings']['selection_and_comparison_seconds']:.4f}s, total {stats['total_seconds']:.4f}s", flush=True)
                equivalent(outputs["baseline"], outputs["optimized"])
                write_json(out / "benchmark.json", evidence)
            summary = {}
            for version in sources:
                runs = [r for r in evidence["runs"] if r["case"] == case and r["version"] == version]
                summary[version] = {}
                for name, values in {
                    "comparison_seconds": [r["performance"]["timings"]["selection_and_comparison_seconds"] for r in runs],
                    "total_seconds": [r["performance"]["total_seconds"] for r in runs],
                    "peak_rss_kib": [r["performance"]["peak_rss_kib"] for r in runs],
                }.items():
                    summary[version][name] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
            summary["comparison_reduction_percent"] = 100 * (1 - summary["optimized"]["comparison_seconds"]["median"] /
                                                               summary["baseline"]["comparison_seconds"]["median"])
            summary["total_reduction_percent"] = 100 * (1 - summary["optimized"]["total_seconds"]["median"] /
                                                          summary["baseline"]["total_seconds"]["median"])
            summary["results_equivalent"] = True
            evidence["cases"][case] = summary
    finally:
        evidence["original_vdb_unchanged"] = fingerprint(args.vdb.resolve()) == before
        evidence["load_average_after"] = os.getloadavg()
        write_json(out / "benchmark.json", evidence)
    require(evidence["original_vdb_unchanged"], "Benchmark changed original VDB")
    print(json.dumps(evidence["cases"], indent=2))


if __name__ == "__main__":
    main()
