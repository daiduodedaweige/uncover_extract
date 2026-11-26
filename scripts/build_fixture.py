#!/usr/bin/env python3
"""Build a reproducible two-test VCS VDB under an explicit output directory."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uncover_extract.common import require, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--vcs-bin",default=shutil.which("vcs"))
    p.add_argument("--metrics",default="line+tgl+branch+cond",help="VCS -cm metric list")
    args=p.parse_args()
    out=args.out.resolve();require(not out.exists(),"Choose a new --out directory")
    require(args.vcs_bin,"VCS is not on PATH; set --vcs-bin")
    out.mkdir(parents=True)
    source=Path(__file__).resolve().parents[1]/"examples/fixture/fixture.sv"
    command=[args.vcs_bin,"-full64","-sverilog","-cm",args.metrics,"-cm_dir","model.vdb",
             "-Mdir=csrc","-o","simv","-top","fixture_tb",str(source),"-l","compile.log"]
    commands=[command]
    with (out/"build_console.log").open("w") as log:
        subprocess.run(command,cwd=out,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        for mode in (0,1):
            command=[str(out/"simv"),"-no_save","-cm",args.metrics,"-cm_name",f"mode{mode}",
                     "-cm_dir","model.vdb",f"+mode={mode}","-l",f"mode{mode}.log"]
            commands.append(command)
            subprocess.run(command,cwd=out,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    write_json(out/"fixture_manifest.json",{"source":str(source),"vdb":str(out/"model.vdb"),
        "origin":"controlled integration fixture, not a production project", "commands":commands})
    print(out/"model.vdb")


if __name__=="__main__": raise SystemExit(main())
