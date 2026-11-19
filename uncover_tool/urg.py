#!/usr/bin/env python3
"""Independently compare full-design native totals against an URG summary."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from .common import ToolError, fingerprint, require, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--urg-bin", default=shutil.which("urg"))
    args=p.parse_args()
    manifest=json.loads((args.result/"analysis_manifest.json").read_text())
    require(not manifest["configuration"]["instance_roots"], "URG checker currently checks full-design runs only")
    require(args.urg_bin, "URG not found; set --urg-bin")
    out=args.out.resolve()
    require(not out.exists(), "URG output already exists; choose a new directory")
    source=Path(manifest["input"]["path"])
    before=fingerprint(source)
    require(before["sha256"]==manifest["input"]["fingerprint"]["sha256"], "VDB has changed since the extraction being checked")
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=".urg-input-",dir=out.parent) as tmp:
        copy=Path(tmp).resolve()/"input.vdb"
        shutil.copytree(source,copy)
        tests=out/"tests.txt"
        tests.write_text("\n".join(str(copy/name) for name in manifest["selected_tests"])+"\n")
        command=[args.urg_bin,"-dir",str(copy),"-tests",str(tests),"-metric","+".join(manifest["summary"]),
                 "-format","text","-show","summary","-show","ratios","-xml_advanced","-report",str(out/"report")]
        paths=manifest["configuration"]["exclude_files"]
        if paths:
            elfiles=out/"elfiles.txt";elfiles.write_text("\n".join(paths)+"\n")
            command.extend(["-elfilelist",str(elfiles)])
        with (out/"urg.log").open("w") as log:
            result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        require(result.returncode==0,f"URG failed; inspect {out/'urg.log'}")
        root=ET.parse(out/"report/session.xml").getroot()
        labels={"Line":"line","Toggle":"tgl","Branch":"branch","Condition":"cond","Cond":"cond"}
        actual={}
        for node in root.findall("./old_coverage/scope[@type='top']/metric"):
            metric=labels.get(node.get("name"))
            if metric:
                covered,total=map(int,node.get("value").split("/"))
                actual[metric]={"covered":covered,"coverable":total}
        comparison={}
        for metric,stats in manifest["summary"].items():
            expected={"covered":stats["native_covered"],"coverable":stats["native_coverable"]}
            comparison[metric]={"npi":expected,"urg":actual.get(metric),"equal":expected==actual.get(metric)}
        unchanged=fingerprint(source)==before
        report={"passed":all(v["equal"] for v in comparison.values()) and unchanged,
                "comparison":comparison,"original_vdb_unchanged":unchanged,"command":command,
                "scope":"same complete design, selected tests, external exclusions; exact native totals, not rounded percentages"}
        write_json(out/"comparison.json",report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report["passed"] else 1


if __name__=="__main__":
    try: raise SystemExit(main())
    except (ToolError,OSError,ValueError) as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
