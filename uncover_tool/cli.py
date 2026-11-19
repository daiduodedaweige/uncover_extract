"""Validate configuration, isolate native code, then publish complete results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__
from .common import METRICS, ToolError, require, write_json


def parser():
    p = argparse.ArgumentParser(description="Extract instance-local VDB coverage holes; NPI reads use an isolated copy.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--vdb", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--rtl-root", action="append", default=[], type=Path)
    p.add_argument("--path-map", action="append", default=[], nargs=2, metavar=("OLD_PREFIX", "NEW_PREFIX"))
    p.add_argument("--instance-root", action="append", default=[])
    p.add_argument("--metrics", nargs="+", type=lambda s: {"toggle": "tgl", "condition": "cond"}.get(s.lower(), s.lower()), choices=METRICS)
    tests = p.add_mutually_exclusive_group()
    tests.add_argument("--tests", nargs="+")
    tests.add_argument("--all-tests", action="store_true")
    p.add_argument("--exclude-file", action="append", default=[], type=Path, help="apply existing external exclusions before extraction")
    p.add_argument("--limit-per-metric", "--limit", type=int, help="positive output limit per metric; omit for all holes")
    p.add_argument("--compare-peers", choices=["none", "selected", "all"], default="selected")
    p.add_argument("--include-other-states", action="store_true", help="exhaustively inventory nonstandard atomic states as gzip JSONL (slower)")
    p.add_argument("--per-metric", action="store_true")
    p.add_argument("--export-exclusions", action="store_true")
    p.add_argument("--validate-exclusions", action="store_true")
    p.add_argument("--validate-urg", action="store_true", help="independent full-design URG totals check before publishing")
    p.add_argument("--urg-bin", default=shutil.which("urg"), help="URG executable (only required with --validate-urg)")
    p.add_argument("--verdi-home", type=Path, default=os.environ.get("VERDI_HOME", "/nfs/tools/synopsys/verdi/U-2023.03"))
    p.add_argument("--work-dir", type=Path)
    p.add_argument("--timeout", type=int, default=3600, help="native worker timeout in seconds (default: 3600)")
    p.add_argument("--overwrite", action="store_true")
    return p


def configuration(args):
    vdb, out = args.vdb.resolve(), args.out.resolve()
    require(vdb.is_dir() and (vdb / "snps/coverage/db").is_dir(), f"Not a coverage VDB directory: {vdb}")
    require(out != vdb and vdb not in out.parents and out not in vdb.parents,
            "Output and input VDB must be separate, non-overlapping directories")
    package = Path(__file__).resolve().parent.parent
    require(out != package and out not in package.parents, "Output cannot replace the tool source directory or its parent")
    require(not args.out.is_symlink(), "Output directory must not be a symbolic link")
    require(args.limit_per_metric is None or args.limit_per_metric > 0, "--limit-per-metric must be positive")
    require(args.timeout > 0, "--timeout must be positive")
    require(not args.validate_urg or not args.instance_root, "--validate-urg currently requires the complete design (omit --instance-root)")
    require(not args.validate_urg or args.urg_bin, "URG not found; set --urg-bin")
    for root in args.rtl_root:
        require(root.is_dir(), f"RTL root does not exist: {root}")
        resolved = root.resolve()
        require(out != resolved and out not in resolved.parents and resolved not in out.parents,
                f"Output cannot overlap an RTL root: {root}")
    for old, new in args.path_map:
        require(old and Path(new).is_dir(), f"Invalid --path-map {old} {new}")
    for path in args.exclude_file:
        require(path.is_file(), f"Exclusion file does not exist: {path}")
        require(out not in path.resolve().parents, "Exclusion input cannot live inside the replaced output directory")
    if args.work_dir:
        work = args.work_dir.resolve()
        require(work != vdb and vdb not in work.parents and work not in vdb.parents,
                "Work directory and input VDB must not overlap")
        require(work != out and out not in work.parents, "Work directory cannot be inside final output")
    if out.exists():
        require(out.is_dir(), f"Output is not a directory: {out}")
        if any(out.iterdir()):
            require(args.overwrite, f"Output already contains files: {out}; choose a new --out or use --overwrite")
            marker = out / "analysis_manifest.json"
            require(marker.is_file(), "--overwrite only replaces a previous uncover-extract result directory")
            try:
                require(json.loads(marker.read_text()).get("tool") == "uncover-extract", "Existing output is not owned by uncover-extract")
            except (ValueError, OSError) as exc:
                raise ToolError(f"Invalid previous manifest: {exc}") from exc
    return {"vdb": str(vdb), "out": str(out), "rtl_roots": [str(p.resolve()) for p in args.rtl_root],
        "path_maps": [[old.rstrip("/"), str(Path(new).resolve())] for old, new in args.path_map],
        "instance_roots": args.instance_root, "metrics": list(dict.fromkeys(args.metrics)) if args.metrics else None,
        "tests": args.tests, "all_tests": args.all_tests, "exclude_files": [str(p.resolve()) for p in args.exclude_file],
        "limit": args.limit_per_metric, "compare_peers": args.compare_peers, "per_metric": args.per_metric,
        "include_other_states": args.include_other_states, "export_exclusions": args.export_exclusions,
        "validate_exclusions": args.validate_exclusions, "verdi_home": str(args.verdi_home.resolve()),
        "validate_urg": args.validate_urg, "urg_bin": args.urg_bin,
        "work_dir": str(args.work_dir.resolve()) if args.work_dir else None}


def execute(config, timeout):
    out = Path(config["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.running-", dir=out.parent))
    work_parent = Path(config["work_dir"]) if config["work_dir"] else out.parent
    work_parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".uncover-work-", dir=work_parent))
    config = {**config, "scratch_dir": str(scratch)}
    config_path = stage / "config.json"
    write_json(config_path, config)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    print(f"Reading an isolated VDB copy. Progress log: {stage / 'execution.log'}", file=sys.stderr, flush=True)
    try:
        with (stage / "execution.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uncover_tool.worker", str(config_path), str(stage)],
                                       stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            try:
                deadline = time.monotonic() + timeout
                with (stage / "execution.log").open(encoding="utf-8", errors="replace") as progress:
                    while True:
                        try:
                            returncode = process.wait(timeout=0.5)
                            break
                        except subprocess.TimeoutExpired:
                            for line in progress:
                                if line.startswith(tuple(metric + ":" for metric in METRICS)):
                                    print(line.rstrip(), file=sys.stderr, flush=True)
                            if time.monotonic() >= deadline:
                                raise
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise ToolError("Native worker timed out or was interrupted; previous results remain unchanged")
        require(returncode == 0 and (stage / "analysis_manifest.json").is_file(),
                f"Native extraction failed (exit {returncode}); inspect {stage / 'execution.log'}")
        config_path.unlink()
        manifest_path = stage / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if config["validate_urg"]:
            command = [sys.executable, "-m", "uncover_tool.urg", "--result", str(stage),
                       "--out", str(stage / "urg_validation"), "--urg-bin", config["urg_bin"]]
            with (stage / "urg_validation.log").open("w") as log:
                checked = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=240)
            require(checked.returncode == 0, f"Independent URG check failed; inspect {stage / 'urg_validation.log'}")
            manifest["urg_validation"] = json.loads((stage / "urg_validation/comparison.json").read_text())
        else:
            manifest["urg_validation"] = {"status": "not_requested"}
        for attempt in range(10):
            try:
                shutil.rmtree(scratch)
                break
            except OSError:
                time.sleep(0.2)
        if scratch.exists():
            manifest["warnings"].append(f"Native logs still open; temporary workspace retained: {scratch}")
        manifest["outputs"] = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
        write_json(manifest_path, manifest)
        previous = None
        if out.exists():
            previous = stage.with_name(stage.name + ".previous")
            out.rename(previous)
        try:
            stage.rename(out)
        except BaseException:
            if previous is not None:
                previous.rename(out)
            raise
        if previous is not None:
            shutil.rmtree(previous)
        print(json.dumps({"status": "success", "out": str(out),
            "records_per_metric": {m: v["output_count"] for m, v in manifest["summary"].items()}}, ensure_ascii=False))
        return 0
    except BaseException as exc:
        failure = {"status": "failed", "created_utc": datetime.now(timezone.utc).isoformat(),
                   "error": str(exc), "requested_output": str(out), "diagnostics": str(stage), "workspace": str(scratch)}
        write_json(stage / "failure.json", failure)
        print(f"Error: {exc}\nFailure diagnostics: {stage}", file=sys.stderr)
        return 1


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = configuration(args)
        return execute(config, args.timeout)
    except (ToolError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
