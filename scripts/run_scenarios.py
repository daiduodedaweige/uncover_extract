#!/usr/bin/env python3
"""Native end-to-end checks against the controlled fixture produced by build_fixture.py."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from uncover_tool.common import require,write_json,fingerprint
from uncover_tool.exclusions import parse_records,render_records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vdb",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--verdi-home",default="/nfs/tools/synopsys/verdi/U-2023.03")
    args=p.parse_args();vdb=args.vdb.resolve();out=args.out.resolve()
    require(not out.exists(),"Choose a new scenario output directory");out.mkdir(parents=True)
    before=fingerprint(vdb)
    evidence=[]

    def execute(name,extra=(),test_args=("--tests","mode0"),expected_code=0,overwrite=False):
        command=[sys.executable,str(ROOT/"uncover_extract.py"),"--vdb",str(vdb),"--out",str(out/name),
                 "--verdi-home",args.verdi_home,"--metrics","line","tgl","branch","cond",*test_args,*extra]
        tick=time.perf_counter()
        with (out/(name+".log")).open("a") as log:
            r=subprocess.run(command,cwd=out.parent,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        require(r.returncode==expected_code,f"Scenario {name} returned {r.returncode}; inspect its log")
        evidence.append({"case":name,"returncode":r.returncode,"seconds":time.perf_counter()-tick,"command":command})
        if expected_code:return None
        manifest=json.loads((out/name/"analysis_manifest.json").read_text())
        records=json.loads((out/name/"uncovered_coverage.json").read_text())["records"]
        return manifest,records

    base,records=execute("full",["--compare-peers","selected","--export-exclusions","--validate-exclusions","--validate-urg"])
    require(any(row.get("same_rtl_coverage",{}).get("incomparable_instances") for row in records),
            "Parameter variants were not separated into incompatible model groups")
    no_peers,no_peer_rows=execute("no-peers",["--compare-peers","none"])
    require(no_peers["performance"]["comparison"]=={"mode":"none"},"Disabled comparison built an index/cache")
    require([r["target_id"] for r in no_peer_rows]==[r["target_id"] for r in records],"Comparison mode changed target selection")
    require(all("same_rtl_coverage" not in r for r in no_peer_rows),"Disabled comparison added peer facts")
    chosen=next(r for r in records if r["same_rtl_coverage"]["comparable_instance_count"]>1)
    scoped,scoped_rows=execute("outside-root-peers",["--instance-root",chosen["instance_path"],"--limit-per-metric","1"])
    require(any(p["instance_path"]!=r["instance_path"] for r in scoped_rows for p in r["same_rtl_coverage"]["instances"]),
            "Scoping extraction incorrectly removed outside-root peers")
    limited,rows=execute("limited",["--limit-per-metric","1"])
    for metric in base["summary"]:
        require([r["target_id"] for r in rows if r["metric"]==metric]==[r["target_id"] for r in records if r["metric"]==metric][:1],
                f"Limit changed stable selection for {metric}")
        require(limited["summary"][metric]["uncovered_candidates"]==base["summary"][metric]["uncovered_candidates"],"Limit changed scan counts")
    merged,_=execute("merged",["--compare-peers","all","--validate-urg"],test_args=["--all-tests"])
    require(any(merged["summary"][m]["native_covered"]>base["summary"][m]["native_covered"] for m in base["summary"]),"Native test merge added no coverage")
    require(merged["same_rtl_comparison"]["targets_with_state_differences"]>0,"Peer target differences not detected")
    empty,empty_rows=execute("empty",["--exclude-file",str(out/"full/extracted_coverage.el"),"--include-other-states"])
    require(not empty_rows,"Excluding all missing targets did not yield an empty result")
    source=records[0]["source_file"];empty_rtl=out/"empty-rtl";empty_rtl.mkdir()
    missing,missing_rows=execute("missing-source",["--path-map",str(Path(source).parent),str(empty_rtl),"--compare-peers","none"])
    require([r["target_id"] for r in missing_rows]==[r["target_id"] for r in records],"Missing source dropped/changed target identities")
    require(all(r["source_mapping"]["status"]=="missing" for r in missing_rows),"Missing-source scenario mapped unexpectedly")
    partial=out/"partial.el"
    first=next(r for r in parse_records((out/"full/extracted_coverage.el").read_text()) if r[2].startswith("Block "))
    partial.write_text(render_records([first]))
    existing,_=execute("existing",["--exclude-file",str(partial),"--include-other-states","--export-exclusions","--validate-exclusions","--validate-urg"])
    require(existing["exclusion_export"]["existing_native_record_count"]>0,"Baseline exclusions were not observed")
    require(existing["exclusion_validation"]["status"]=="passed","Existing exclusion validation failed")
    execute("ambiguous-tests",test_args=[],expected_code=1)
    execute("wrong-test",test_args=["--tests","not-a-test"],expected_code=1)
    execute("wrong-instance",["--instance-root","no.such.instance"],expected_code=1)
    old=(out/"limited/analysis_manifest.json").read_bytes()
    execute("limited",["--overwrite","--verdi-home",str(out/"no-verdi")],expected_code=1)
    require((out/"limited/analysis_manifest.json").read_bytes()==old,"Failed overwrite changed previous successful results")
    require(fingerprint(vdb)==before,"Integration scenarios changed the original fixture VDB")
    result={"passed":True,"cases":evidence,"original_vdb_unchanged":True,
            "fixture_origin":"controlled VCS design; supplementary to production-project validation"}
    write_json(out/"scenario_results.json",result)
    print(json.dumps({"passed":True,"cases":len(evidence),"out":str(out)}))


if __name__=="__main__": raise SystemExit(main())
