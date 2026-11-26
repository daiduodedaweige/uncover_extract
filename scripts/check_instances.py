#!/usr/bin/env python3
"""Verify complete per-instance pages and optional reference fact equivalence."""
import argparse
import gzip
import hashlib
from functools import lru_cache
from itertools import zip_longest
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from uncover_tool.common import require,write_json
from uncover_tool.facts import FactsReader
from uncover_tool.output import cell,source_cells,target_description


def stream_records(path):
    opener=gzip.open if path.suffix=='.gz' else Path.open
    with opener(path,'rt',encoding='utf-8') as stream:
        for line in stream:yield json.loads(line)


def equal_rows(left,right,label):
    missing=object()
    for first,second in zip_longest(left,right,fillvalue=missing):
        require(first==second,f'{label} differs')


def compare(reference,result):
    readers=[FactsReader(Path(path)/'coverage_facts.sqlite') for path in (reference,result)]
    try:
        for key in ('database_id','test_scope_ref'):
            require(readers[0].metadata[key]==readers[1].metadata[key],f'Metadata differs: {key}')
        # IDs and table layout may change. Compare every instance's actual descriptor
        # identity and lossless raw values, including covered and special states.
        count=0
        paths=[{path:iid for iid,path in reader.instances.items()} for reader in readers]
        require(paths[0].keys()==paths[1].keys(),'Selected instances differ')
        def states(reader,iid):
            query=('SELECT m.metric,src.path,t.locator,s.covered,s.coverable,s.raw FROM states s '
                   'JOIN targets t ON t.id=s.target JOIN models m ON m.id=t.model JOIN sources src ON src.id=t.source '
                   'WHERE s.instance=? ORDER BY m.metric,src.path,t.locator')
            for metric,source,locator,covered,total,raw in reader.db.execute(query,(iid,)):
                yield metric,source,locator,int(covered),int(total),int(raw)
        for path in paths[0]:
            first,second=[states(reader,mapping[path]) for reader,mapping in zip(readers,paths)]
            missing=object()
            for a,b in zip_longest(first,second,fillvalue=missing):
                require(a==b,f'Instance raw state differs: {path}')
                count+=1
        return {'equal':True,'instance_atomic_states':count}
    finally:
        for reader in readers:reader.close()


def verify(result):
    result=Path(result).resolve()
    data=json.loads((result/'instance_summary.json').read_text())
    manifest=json.loads((result/'analysis_manifest.json').read_text())
    require('same_rtl_comparison' not in manifest,'Obsolete comparison manifest field')
    for name in ('differences.csv','differences.jsonl.gz','difference_patterns.jsonl.gz','module_summary.json'):
        require(not (result/name).exists(),f'Obsolete comparison output: {name}')
    reader=FactsReader(result/'coverage_facts.sqlite')
    @lru_cache(128)
    def source_link(sid,directory):
        source,mapping=reader.sources[sid]
        return source_cells(source,None,mapping,directory)[0]
    total,pages,links=0,0,set()
    main=(result/'report.md').read_text()
    anchors=set(re.findall(r'<a id="([^"]+)"></a>',main))
    require(len(anchors)==len(data['instances']),'Instance anchors are missing or duplicate')
    def check_links(path,text):
        for destination in set(re.findall(r'\]\(([^)]+)\)',text)):
            if destination.startswith('#'):
                require(unquote(destination[1:]) in anchors,'Broken instance anchor')
                continue
            target=(path.parent/unquote(destination)).resolve()
            if target in links:continue
            require(target.is_file(),f'Broken file link in {path}: {destination}')
            links.add(target)
    check_links(result/'report.md',main)
    expected={row['instance_path']:row for row in reader.summaries()}
    try:
        for row in data['instances']:
            path=row['instance_path']
            require(row['metrics'].keys()==expected[path]['metrics'].keys(),'Metric list differs')
            has_holes=any(item['uncovered_count'] for item in row['metrics'].values())
            require(('directory' in row)==has_holes,'Empty instance directory recorded')
            if has_holes:
                index=result/row['directory']/'index.md'
                check_links(index,index.read_text())
            for metric,item in row['metrics'].items():
                require(item['uncovered_count']==expected[path]['metrics'][metric]['uncovered_count'],'Summary differs from states')
                points=iter(reader.holes(row['instance_id'],metric)) if item['pages'] else iter(())
                observed=0
                digest=hashlib.sha256() if 'target_sequence_sha256' in item else None
                show_target=metric!='line'
                table=('| 源码文件 | 行号 |'+(' 信号 / 覆盖点 / 方向 |' if show_target else '')+'\n'
                       '| --- | ---: |'+(' --- |' if show_target else '')+'\n')
                legacy_table=('| 目标编号 | 源码文件 | 行号 |'+(' 信号 / 覆盖点 / 方向 |' if show_target else '')+'\n'
                              '| ---: | --- | ---: |'+(' --- |' if show_target else '')+'\n')
                for page in item['pages']:
                    file=result/page['path'];text=file.read_text()
                    checklist='## 未覆盖点\n\n' in text
                    numbered=legacy_table in text
                    page_table='## 未覆盖点\n\n' if checklist else legacy_table if numbered else table
                    require(page_table in text,'Coverage table columns differ')
                    records=text.split(page_table,1)[1].split('\n\n',1)[0].splitlines()
                    require(len(records)==page['records'],'Page record count differs')
                    for record in records:
                        fact=next(points,None)
                        require(fact is not None,'Page has extra coverage points')
                        source=source_link(fact['source'],file.parent)
                        if checklist:
                            require(re.match(r'^- \[[ xX]\] ',record) is not None,'Invalid analysis checkbox')
                            record=record[6:]
                            expected_record=f"源码文件：{source}；行号：{cell(fact['line'])}"
                        else:
                            number=f"{fact['id']} | " if numbered else ''
                            expected_record=f"| {number}{source} | {cell(fact['line'])} |"
                        if show_target:
                            description=fact['description']
                            if description is None:description=target_description(metric,json.loads(fact['trail']))
                            expected_record+=(f'；信号 / 覆盖点 / 方向：{cell(description)}' if checklist
                                              else f' {cell(description)} |')
                        require(record==expected_record,'Page missing, duplicate, changed or reordered coverage point')
                        if digest is not None:digest.update(f"{fact['id']}\n".encode())
                    check_links(file,text)
                    require(file.stat().st_size==page['bytes'],'Page byte count differs')
                    observed+=len(records);pages+=1
                require(next(points,None) is None,'Coverage points were omitted')
                require(observed==item['uncovered_count'],'Incomplete metric pages')
                if observed and digest is not None:
                    require(digest.hexdigest()==item['target_sequence_sha256'],'Page digest differs')
                total+=observed
            print(f"verify: {path}, {total} uncovered records checked",flush=True)
        require(total==data['statistics']['uncovered_records'],'Report total differs')
        for metric,count in data['statistics']['uncovered_by_metric'].items():
            if metric in manifest['summary']:
                require(count==manifest['summary'][metric]['uncovered_candidates'],'Manifest total differs')
        require(set(data['statistics'])==set(manifest['report']),'Report statistics schema differs')
        return {'passed':True,'instances':len(data['instances']),'uncovered_records':total,'detail_pages':pages,
                'checked_link_targets':len(links)}
    finally:reader.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.out:require(not args.out.exists(),'Choose a new verification JSON file')
    checked=verify(args.result)
    if args.reference:checked['reference']=compare(args.reference,args.result)
    if args.out:write_json(args.out,checked)
    print(json.dumps(checked))


if __name__=='__main__':main()
