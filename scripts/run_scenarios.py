#!/usr/bin/env python3
"""Native end-to-end checks for per-instance coverage extraction and reports."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from uncover_tool.common import require,write_json,fingerprint
from uncover_tool.facts import FactsReader
from scripts.check_instances import compare,verify,stream_records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vdb',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--exclude-file',type=Path)
    parser.add_argument('--urg-bin',default='/nfs/tools/synopsys/vcs/U-2023.03/bin/urg')
    args=parser.parse_args()
    vdb,out=args.vdb.resolve(),args.out.resolve()
    require(not out.exists(),'Choose a new scenario output directory');out.mkdir(parents=True)
    before,evidence,checks=fingerprint(vdb),[],{}
    def execute(name,extra=(),tests=('--tests','mode0'),expected=0):
        command=[sys.executable,str(ROOT/'uncover_extract.py'),'--vdb',str(vdb),'--out',str(out/name),
                 '--metrics','line','tgl','branch','cond',*tests,*extra]
        tick=time.perf_counter()
        with (out/(name+'.log')).open('a') as log:
            run=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        require(run.returncode==expected,f'{name} returned {run.returncode}; inspect its log')
        evidence.append({'case':name,'returncode':run.returncode,'seconds':time.perf_counter()-tick,'command':command})
        if expected:return
        result=out/name
        checks[name]=verify(result)
        manifest=json.loads((result/'analysis_manifest.json').read_text())
        require(manifest['schema_version']=='3.0','Unexpected manifest version')
        reader=FactsReader(result/'coverage_facts.sqlite')
        try:
            holes=list(reader.details())
            require(reader.db.execute('SELECT COUNT(*) FROM states WHERE (raw & 68) != 0').fetchone()[0]==0,'Excluded states retained')
        finally:reader.close()
        require(len(holes)==sum(s['uncovered_candidates'] for s in manifest['summary'].values()),'Missing holes')
        return manifest,holes
    urg=['--validate-urg','--urg-bin',args.urg_bin]
    base,holes=execute('full',urg)
    require(base['urg_validation']['passed'],'URG failed')
    merged,_=execute('merged',urg,('--all-tests',))
    require(any(merged['summary'][m]['native_covered']>base['summary'][m]['native_covered'] for m in base['summary']),'Test merge added no coverage')
    _,scoped=execute('scoped',['--instance-root','fixture_tb.left'])
    require(all(r['instance_path']=='fixture_tb.left' for r in scoped),'Out-of-scope holes')
    for size in (1,500):
        name=f'batch-{size}';execute(name,['--batch-size',str(size)])
        checks[name]['equivalence']=compare(out/'full',out/name)
    empty=out/'empty-rtl';empty.mkdir()
    source=holes[0]['source_file']
    _,missing=execute('missing-source',['--path-map',str(Path(source).parent),str(empty)])
    require([r['target_id'] for r in holes]==[r['target_id'] for r in missing],'Missing RTL changed facts')
    require(all(r['source_mapping']['status']=='missing' for r in missing),'RTL unexpectedly resolved')
    if args.exclude_file:
        _,excluded=execute('existing',['--exclude-file',str(args.exclude_file.resolve()),*urg])
        require(len(excluded)<len(holes),'Exclusions did not remove holes')
    destination=out/'holes.jsonl.gz'
    subprocess.run([sys.executable,'-m','uncover_tool.export_facts','--result',str(out/'full'),'--out',str(destination)],cwd=ROOT,check=True,timeout=30)
    require(list(stream_records(destination))==holes,'Offline export changed facts')
    execute('ambiguous-tests',tests=(),expected=1)
    execute('wrong-test',tests=('--tests','not-a-test'),expected=1)
    execute('wrong-instance',['--instance-root','no.such.instance'],expected=1)
    old=(out/'full/analysis_manifest.json').read_bytes()
    execute('full',['--overwrite','--verdi-home',str(out/'no-verdi')],expected=1)
    require((out/'full/analysis_manifest.json').read_bytes()==old,'Failed overwrite changed published results')
    require(fingerprint(vdb)==before,'Scenarios changed original VDB')
    write_json(out/'scenario_results.json',{'passed':True,'cases':evidence,'checks':checks,'original_vdb_unchanged':True})
    print(json.dumps({'passed':True,'cases':len(evidence),'out':str(out)}))


if __name__=='__main__':main()
