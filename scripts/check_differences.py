#!/usr/bin/env python3
"""Compare independent module analysis against the existing all-target path."""
import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import fingerprint, require, stable_id, write_json
from uncover_tool.differences import FactsReader


def key(row):
    return row["metric"], row["source_file"], stable_id(row["native_locator"]), tuple(
        p["instance_path"] for p in row["same_rtl_coverage"]["instances"])


def facts(row):
    peers = row["same_rtl_coverage"]
    return (tuple((p["instance_path"], p["covered"], p["coverable"], p["raw_status"], p["status"])
                  for p in peers["instances"]),
            tuple(p["instance_path"] for p in peers["incomparable_instances"]))


def compare(reference, result):
    with gzip.open(reference / "same_rtl_comparison.jsonl.gz", "rt") as stream:
        expected = {key(row): facts(row) for row in map(json.loads, stream)}
    reader = FactsReader(result / "coverage_facts.sqlite")
    try:
        actual = {key(row): facts(row) for row in reader.details()
                  if row["same_rtl_coverage"]["same_target_differs"] or row["same_rtl_coverage"]["incomparable_instances"]}
        require(expected == actual, f"Target comparison differs: {len(expected)} reference, {len(actual)} new")
        old_holes = json.loads((reference / "uncovered_coverage.json").read_text())["records"]
        old = {r["target_id"]: (r["covered"], r["coverable"], r["raw_status"]) for r in old_holes}
        new = {r["target_id"]: (r["covered"], r["coverable"], r["raw_status"]) for r in reader.details("holes")}
        require(old == new, "Offline holes export changed identity or native facts")
        before = json.loads((reference / "analysis_manifest.json").read_text())
        after = json.loads((result / "analysis_manifest.json").read_text())
        for metric in before["summary"]:
            for field in ("native_covered", "native_coverable", "uncovered_candidates"):
                require(before["summary"][metric][field] == after["summary"][metric][field],
                        f"{metric}/{field} changed")
        require(before["same_rtl_comparison"]["unique_model_targets_checked"] ==
                after["same_rtl_comparison"]["unique_model_targets_checked"], "Unique model-target count changed")
        return {"comparison_records": len(actual), "holes_round_trip": len(new), "equal": True}
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vdb", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exclude-file", type=Path)
    args = parser.parse_args()
    vdb, out = args.vdb.resolve(), args.out.resolve()
    require(not out.exists(), "Choose a new validation output directory")
    out.mkdir(parents=True)
    before = fingerprint(vdb)
    cases = [("full", ["--tests", "mode0"]), ("merged", ["--all-tests"]),
             ("scoped", ["--tests", "mode0", "--instance-root", "fixture_tb.left"])]
    if args.exclude_file:
        cases.append(("excluded", ["--tests", "mode0", "--exclude-file", str(args.exclude_file.resolve())]))
    evidence = []
    with tempfile.TemporaryDirectory(prefix="module-differences-check-") as work:
        for name, extra in cases:
            runs = {}
            for mode in ("holes", "differences"):
                result = out / f"{name}-{mode}"
                command = [sys.executable, str(ROOT / "uncover_extract.py"), "--mode", mode, "--vdb", str(vdb),
                           "--out", str(result), "--metrics", "line", "tgl", "branch", "cond", "--work-dir", work,
                           "--compare-peers", "all", *extra]
                tick = time.perf_counter()
                with (out / f"{name}-{mode}.log").open("w") as log:
                    run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
                require(run.returncode == 0, f"Failed {name}/{mode}; inspect its log")
                runs[mode] = {"command": command, "seconds": time.perf_counter() - tick}
            checked = compare(out / f"{name}-holes", out / f"{name}-differences")
            evidence.append({"case": name, **checked, "runs": runs})
            print(json.dumps({"case": name, **checked}), flush=True)
    require(fingerprint(vdb) == before, "Original fixture VDB changed")
    write_json(out / "checks.json", {"passed": True, "cases": evidence, "original_vdb_unchanged": True})


if __name__ == "__main__":
    main()
