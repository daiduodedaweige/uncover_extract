"""Instance-local facts, complete reports, offline migration and publication tests."""
from contextlib import nullcontext, redirect_stdout
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from test_core import Node
from uncover_tool.common import ToolError, stable_id, walk_bins, write_json
from uncover_tool.extraction import analyze
from uncover_tool.facts import FactsReader, FactStore
from uncover_tool.mapping import SourceMapper
from uncover_tool.output import instance_directory, instance_parts
from uncover_tool.report import write_report
from uncover_tool.rebuild_report import rebuild
from uncover_tool.export_facts import main as export


class Session:
    test = None
    selected_tests = ["test"]
    available_tests = ["test"]
    warnings = []

    def __init__(self, root, states, models=None, names=None):
        self.vdb = root
        self.instances = {path: Node(path, source="same.sv") for path in states}
        self.models = models if models is not None else {path: "model" for path in states}
        self.roots = {}
        self.released, self.accessed = [], []
        self.cov = type("Cov", (), {"release_handle": self.released.append})()
        for path, values in states.items():
            leaves = [Node((names or {}).get(path, {}).get(i, str(i)), kind="npiCovStmtBin", source="same.sv",
                           covered=v[0], coverable=v[1], raw=v[2]) for i, v in enumerate(values)]
            self.roots[path] = Node("metric", kind="npiCovLineMetric", source="same.sv", children=leaves,
                                   covered=sum(v[0] for v in values), coverable=sum(v[1] for v in values))

    def handle(self, path, metric):
        self.accessed.append((path, metric))
        return self.roots[path] if metric == "line" else None

    def info(self, path, metric):
        self.accessed.append((path, metric))
        return {"model_key": self.models[path], "module_name": "Same"}

    def bins(self, path, metric):
        return walk_bins(self.roots[path], "npiCovStmtBin", release=self.released.append)


class InstanceTests(unittest.TestCase):
    def run_analysis(self, session, root, roots=(), metrics=("line",)):
        stage, work = root / "stage", root / "work"
        stage.mkdir(); work.mkdir()
        config = {"metrics": list(metrics), "instance_roots": list(roots), "batch_size": 2}
        with redirect_stdout(io.StringIO()):
            summary, capabilities, stats = analyze(session, config, stage, work, SourceMapper(), "input", {})
        write_json(stage / 'analysis_manifest.json', {'tool': 'uncover-extract', 'summary': summary,
                   'configuration': config, 'selected_tests': ['test']})
        return stage, summary, stats

    def test_all_holes_reported_without_classification_or_peer_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = Session(root, {'top.a': [(1,1,8),(0,1,0),(0,1,0),(0,0,4)],
                                     'top.b': [(1,1,8),(0,1,128),(1,1,8),(0,0,4)]})
            stage, summary, stats = self.run_analysis(session, root)
            self.assertEqual(summary['line']['uncovered_candidates'], 3)
            self.assertEqual(stats['native_atomic_state_reads'], 8)
            reader = FactsReader(stage / 'coverage_facts.sqlite')
            try:
                tables = {r[0] for r in reader.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse(tables & {'classifications','patterns','findings'})
                self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM states').fetchone()[0], 6)
                self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM targets').fetchone()[0], 3)
                holes = list(reader.details())
                self.assertEqual(len(holes), 3)
                self.assertEqual({r['raw_status'] for r in holes}, {0,128})
                for row in holes:
                    self.assertEqual(row['target_id'], stable_id(['input', stable_id(['test']),
                        row['instance_path'], 'line', row['native_locator']]))
            finally:
                reader.close()
            with redirect_stdout(io.StringIO()):
                stats = write_report(stage, {})
            self.assertEqual(stats['uncovered_records'], 3)
            text = (stage/'report.md').read_text()
            self.assertIn('| 模块 | 实例 | Line 未覆盖点数 |', text)
            self.assertNotIn('差异', text)
            self.assertFalse((stage/'differences.csv').exists())
            for page in stage.rglob('line-*.md'):
                detail=page.read_text()
                self.assertIn('| 源码文件 | 行号 |\n| --- | ---: |',detail)
                self.assertNotIn('目标编号',detail)

    def test_scope_does_not_read_outside_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            session=Session(root, {'top.a':[(0,1,0)], 'top.b':[(1,1,8)]})
            stage, _, stats=self.run_analysis(session,root,['top.a'])
            self.assertEqual(stats['native_atomic_state_reads'],1)
            self.assertTrue(all(path=='top.a' for path,metric in session.accessed))
            reader=FactsReader(stage/'coverage_facts.sqlite')
            try:
                self.assertEqual(list(reader.instances.values()),['top.a'])
            finally: reader.close()

    def test_another_instances_status_does_not_change_own_holes(self):
        with tempfile.TemporaryDirectory() as tmp:
            values=[]
            for label, peer in [('before',(0,1,0)),('after',(1,1,8))]:
                root=Path(tmp)/label;root.mkdir()
                stage,_,_=self.run_analysis(Session(root,{'top.a':[(0,1,0)],'top.b':[peer]}),root)
                reader=FactsReader(stage/'coverage_facts.sqlite')
                try: values.append([r for r in reader.details() if r['instance_path']=='top.a'])
                finally: reader.close()
            self.assertEqual(values[0],values[1])

    def test_exclusions_filtered_without_shifting_other_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,summary,_=self.run_analysis(Session(root, {'top.a':[(0,0,4),(0,1,0),(0,0,64)],
                                                            'top.b':[(0,1,0),(0,0,64),(0,0,4)]}),root)
            self.assertEqual(summary['line']['uncovered_candidates'],2)
            reader=FactsReader(stage/'coverage_facts.sqlite')
            try:
                self.assertEqual(len(list(reader.details())),2)
                self.assertEqual(reader.db.execute('SELECT COUNT(*) FROM states WHERE status="excluded"').fetchone()[0],0)
            finally: reader.close()

    def test_all_excluded_points_have_no_detail_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,summary,_=self.run_analysis(Session(root,{'top.a':[(0,0,4),(0,0,64)]}),root)
            self.assertEqual(summary['line']['visited_atomic_bins'],0)
            stats=write_report(stage,{})
            self.assertEqual(stats['instances'],1)
            self.assertEqual(stats['detail_pages'],0)
            self.assertFalse((stage/'reports').exists())

    def test_unknown_identity_and_descriptor_mismatches_keep_all_own_facts(self):
        for models,names in [({'top.a':None,'top.b':None},None),
                             (None,{'top.b':{0:'changed'}})]:
            with self.subTest(models=models),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                stage,_,_=self.run_analysis(Session(root,{'top.a':[(0,1,0),(0,1,0)],'top.b':[(0,1,0)]},models,names),root)
                reader=FactsReader(stage/'coverage_facts.sqlite')
                try:self.assertEqual(len(list(reader.details())),3)
                finally:reader.close()

    def test_four_metrics_special_states_and_per_instance_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            counts={'line':(1,2),'tgl':(2,0),'branch':(0,3),'cond':(3,4)}
            sessions={metric:Session(root,{path:[(0,1,0)]*count+[(1,1,8)]*(4-count)+[(0,1,1),(0,1,2)]
                      for path,count in zip(('top.a','top.b','top.special'),(*holes,0))}) for metric,holes in counts.items()}
            bins={metric:s.bins for metric,s in sessions.items()}
            session=sessions['line']
            session.handle=lambda path,metric: sessions[metric].roots[path] if not (path=='top.special' and metric=='cond') else None
            session.bins=lambda path,metric:bins[metric](path,metric)
            stage,summary,_=self.run_analysis(session,root,metrics=tuple(counts))
            with redirect_stdout(io.StringIO()):stats=write_report(stage,{})
            self.assertEqual(stats['instances_with_holes'],2)
            self.assertEqual(stats['uncovered_by_metric'],{metric:sum(holes) for metric,holes in counts.items()})
            report=json.loads((stage/'instance_summary.json').read_text())
            special=next(row for row in report['instances'] if row['instance_path']=='top.special')
            self.assertEqual(special['metrics']['cond']['status'],'not_collected_or_unavailable')
            self.assertNotIn('directory',special)
            self.assertIn('unreachable',special['metrics']['line']['states'])
            for row in report['instances']:
                for metric,item in row['metrics'].items():
                    self.assertNotIn('target_sequence_sha256',item)
                    for page in item['pages']:
                        text=(stage/page['path']).read_text()
                        header='| 源码文件 | 行号 |'+(' 信号 / 覆盖点 / 方向 |' if metric!='line' else '')
                        self.assertIn(header+'\n',text)
                        self.assertNotIn('目标编号',text)

    def test_pages_complete_ordered_and_navigation_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            names={'top.exu_2':{0:'bit[10]',1:'bit[2]',2:'bit[1]',3:'bit[0]'}}
            session=Session(root,{'top.exu_10':[(1,1,8)],'top.exu_2':[(0,1,0)]*4},names=names)
            handle,bins=session.handle,session.bins
            session.handle=lambda path,metric:handle(path,'line')
            session.bins=lambda path,metric:bins(path,'line')
            stage,_,_=self.run_analysis(session,root,metrics=('tgl',))
            with redirect_stdout(io.StringIO()):write_report(stage,{},page_size=2,page_bytes=4096)
            data=json.loads((stage/'instance_summary.json').read_text())
            self.assertEqual([r['instance_path'] for r in data['instances']],['top.exu_2','top.exu_10'])
            pages=data['instances'][0]['metrics']['tgl']['pages']
            self.assertEqual([p['records'] for p in pages],[2,2])
            self.assertFalse((stage/instance_directory('top.exu_10')).exists())
            descriptions=[]
            for p in pages:
                text=(stage/p['path']).read_text()
                self.assertNotIn('目标编号',text)
                descriptions.extend(re.findall(r'^\| same\.sv \| 3 \| (.+) \|$',text,re.M))
                for target in re.findall(r'\]\(([^)]+)\)',text):
                    self.assertTrue(((stage/p['path']).parent/unquote(target)).is_file(),target)
            self.assertEqual(descriptions,['bit[0]','bit[1]','bit[2]','bit[10]'])

    def test_parent_directory_can_contain_child_without_parent_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,_,_=self.run_analysis(Session(root,{'top':[(1,1,8)],'top.child':[(0,1,0)]}),root)
            with redirect_stdout(io.StringIO()):write_report(stage,{})
            self.assertTrue((stage/instance_directory('top')).is_dir())
            self.assertFalse((stage/instance_directory('top')/'index.md').exists())
            self.assertTrue((stage/instance_directory('top.child')/'index.md').is_file())

    def test_byte_limit_preserves_long_rows_and_same_line_bins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            session=Session(root,{'top.a':[(0,1,0)]*5},names={'top.a':{i:'x'*800+str(i) for i in range(5)}})
            handle=session.handle; bins=session.bins
            session.handle=lambda path,metric:handle(path,'line')
            session.bins=lambda path,metric:bins(path,'line')
            stage,_,_=self.run_analysis(session,root,metrics=('tgl',))
            with redirect_stdout(io.StringIO()):stats=write_report(stage,{},page_size=100,page_bytes=3000)
            self.assertEqual(stats['uncovered_records'],5)
            self.assertGreater(stats['detail_pages'],1)
            self.assertLessEqual(stats['max_detail_page_bytes'],3000)

    def test_raw_large_and_negative_numbers_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=FactStore(Path(tmp)/'facts.sqlite',1)
            try:
                store.add_state(1,1,(-99,1<<80,1<<81));store.flush(True)
                self.assertEqual(store.states([1],[1]),{1:{1:(-99,1<<80,1<<81)}})
            finally:store.close()

    def test_offline_export_and_removed_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,_,_=self.run_analysis(Session(root,{'top.a':[(0,1,0)]*2}),root)
            out=root/'holes.jsonl.gz'
            with redirect_stdout(io.StringIO()):export(['--result',str(stage),'--out',str(out)])
            with gzip.open(out,'rt') as f:self.assertEqual(len(list(f)),2)
            with self.assertRaises(ToolError):export(['--result',str(stage),'--out',str(root/'bad'),'--kind','differences'])
            self.assertFalse((root/'bad').exists())

    def test_offline_rebuild_legacy_facts_without_comparison_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,_,_=self.run_analysis(Session(root,{'top.a':[(0,1,0),(1,1,8)],'top.empty':[]}),root)
            # A legacy facts file needs no classifications or patterns to rebuild.
            db=sqlite3.connect(stage/'coverage_facts.sqlite')
            for table,columns in [('instances','id,path,selected'),('targets','id,model,ordinal,source,line,locator,trail,representative,selected'),
                                  ('states','instance,target,covered,coverable,raw')]:
                db.execute(f'CREATE TABLE old_{table} AS SELECT {columns} FROM {table}')
                db.execute(f'DROP TABLE {table}')
                db.execute(f'ALTER TABLE old_{table} RENAME TO {table}')
            db.execute('UPDATE metadata SET value=? WHERE key="schema_version"',(json.dumps('1.0'),));db.commit();db.close()
            before=(stage/'coverage_facts.sqlite').read_bytes()
            with redirect_stdout(io.StringIO()):result=rebuild(stage,root/'rebuilt')
            self.assertEqual(result['report']['uncovered_records'],1)
            self.assertEqual(before,(stage/'coverage_facts.sqlite').read_bytes())
            self.assertNotIn('same_rtl_comparison',result)
            self.assertEqual(result['facts_source']['schema_version'],'1.0')
            with self.assertRaises(ToolError):rebuild(stage,root/'rebuilt')

    def test_worker_failure_audits_input_and_closes_facts(self):
        from uncover_tool import worker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.vdb';scratch=root/'scratch';stage=root/'stage'
            for path in (original,scratch,stage):path.mkdir()
            (original/'fact').write_text('original')
            session=Session(original,{'top.a':[(0,1,0)]})
            def broken(*args):raise RuntimeError('native read failed')
            session.bins=broken
            config={'vdb':str(original),'scratch_dir':str(scratch),'rtl_roots':[],'path_maps':[],
                    'metrics':['line'],'instance_roots':[]}
            cwd=Path.cwd()
            try:
                with patch.object(worker,'NativeSession',return_value=nullcontext(session)):
                    with self.assertRaisesRegex(RuntimeError,'native read failed'):worker.run(config,stage)
            finally:os.chdir(cwd)
            self.assertTrue(json.loads((stage/'input_audit.json').read_text())['original_unchanged'])
            self.assertFalse((stage/'coverage_facts.sqlite').exists())
            db=sqlite3.connect(scratch/'coverage_facts.sqlite',timeout=0)
            try:db.execute('BEGIN EXCLUSIVE')
            finally:db.close()

    def test_escaped_instance_names_keep_components_unique(self):
        names=['top.x[1]','top.x%5B1%5D','top.模块 空格','top.\\escaped.with.dot .child','top.index.md']
        dirs=[instance_directory(name) for name in names]
        self.assertEqual(len(set(dirs)),len(names))
        self.assertEqual(instance_parts(names[3]),['top','\\escaped.with.dot ','child'])
        self.assertTrue(all('..' not in directory.parts for directory in dirs))

    def test_unicode_special_source_paths_and_instance_anchors_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'源码 [1]#file.sv';source.write_text('module m; endmodule')
            session=Session(root,{'top.模块 空格':[(0,1,0)]})
            for node in [session.roots['top.模块 空格'],*session.roots['top.模块 空格'].child_handles()]:
                values=list(node.values);values[5]=str(source);node.values=tuple(values)
            stage,_,_=self.run_analysis(session,root)
            with redirect_stdout(io.StringIO()):write_report(stage,{})
            main=(stage/'report.md').read_text()
            anchors=set(re.findall(r'<a id="([^"]+)"></a>',main))
            for page in stage.rglob('*.md'):
                for link in re.findall(r'\]\(([^)]+)\)',page.read_text()):
                    if link.startswith('#'):
                        self.assertIn(unquote(link[1:]),anchors)
                    else:
                        self.assertTrue((page.parent/unquote(link)).is_file(),link)

    def test_successful_worker_manifest_has_only_instance_contract(self):
        from uncover_tool import worker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'input.vdb';stage=root/'stage';scratch=root/'scratch'
            for path in (original,stage,scratch):path.mkdir()
            (original/'fact').write_text('input')
            config={'vdb':str(original),'scratch_dir':str(scratch),'rtl_roots':[],'path_maps':[],
                    'metrics':['line'],'instance_roots':[],'verdi_home':'unused'}
            cwd=Path.cwd()
            try:
                with patch.object(worker,'NativeSession',return_value=nullcontext(Session(original,{'top.a':[(0,1,0)]}))),redirect_stdout(io.StringIO()):
                    worker.run(config,stage)
            finally:os.chdir(cwd)
            manifest=json.loads((stage/'analysis_manifest.json').read_text())
            self.assertEqual(manifest['schema_version'],'3.0')
            self.assertEqual(manifest['report']['uncovered_records'],1)
            self.assertNotIn('same_rtl_comparison',manifest)
            self.assertIn('extraction',manifest['performance'])

    def test_no_partial_report_published_after_invalid_offline_page_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage,_,_=self.run_analysis(Session(root,{'top.a':[(0,1,0)]}),root)
            before=(stage/'coverage_facts.sqlite').read_bytes()
            with self.assertRaises(ToolError):rebuild(stage,root/'invalid',page_size=0)
            self.assertFalse((root/'invalid').exists())
            self.assertEqual(before,(stage/'coverage_facts.sqlite').read_bytes())

    def test_verifier_accepts_legacy_tables_and_checks_ids_content_and_digest(self):
        from scripts.check_instances import verify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            session=Session(root,{'top.a':[(0,1,0)]*4},names={'top.a':{i:f'bit[{i}]' for i in range(4)}})
            handle=session.handle
            session.handle=lambda path,metric:handle(path,'line')
            stage,_,_=self.run_analysis(session,root,metrics=('line','tgl','branch','cond'))
            with redirect_stdout(io.StringIO()):stats=write_report(stage,{},page_size=2)
            manifest=json.loads((stage/'analysis_manifest.json').read_text())
            manifest['report']=stats;write_json(stage/'analysis_manifest.json',manifest)
            with redirect_stdout(io.StringIO()):self.assertTrue(verify(stage)['passed'])
            data=json.loads((stage/'instance_summary.json').read_text())
            reader=FactsReader(stage/'coverage_facts.sqlite')
            try:
                for row in data['instances']:
                    for metric,item in row['metrics'].items():
                        points=iter(reader.holes(row['instance_id'],metric))
                        digest=hashlib.sha256()
                        suffix=' 信号 / 覆盖点 / 方向 |' if metric!='line' else ''
                        separator=' --- |' if metric!='line' else ''
                        table=f'| 源码文件 | 行号 |{suffix}\n| --- | ---: |{separator}\n'
                        legacy=f'| 目标编号 | 源码文件 | 行号 |{suffix}\n| ---: | --- | ---: |{separator}\n'
                        for page in item['pages']:
                            file=stage/page['path']
                            before,body=file.read_text().split(table,1)
                            records,after=body.split('\n\n',1)
                            numbered=[]
                            for record in records.splitlines():
                                tid=next(points)['id']
                                numbered.append(f'| {tid} '+record)
                                digest.update(f'{tid}\n'.encode())
                            file.write_text(before+legacy+'\n'.join(numbered)+'\n\n'+after)
                            page['bytes']=file.stat().st_size
                        self.assertIsNone(next(points,None))
                        item['target_sequence_sha256']=digest.hexdigest()
            finally:reader.close()
            sizes=[page['bytes'] for row in data['instances'] for item in row['metrics'].values() for page in item['pages']]
            data['statistics'].update(detail_bytes=sum(sizes),max_detail_page_bytes=max(sizes))
            manifest['report']=data['statistics']
            write_json(stage/'instance_summary.json',data);write_json(stage/'analysis_manifest.json',manifest)
            original={file:file.read_bytes() for file in stage.rglob('*') if file.is_file()}
            with redirect_stdout(io.StringIO()):checked=verify(stage)
            self.assertEqual((checked['uncovered_records'],checked['detail_pages']),(16,8))
            self.assertTrue(all(file.read_bytes()==content for file,content in original.items()))
            for metric,old,new in [('line','| 1 |','| 2 |'),('tgl','bit[0]','bit[9]')]:
                with self.subTest(corruption=metric):
                    file=stage/instance_directory('top.a')/f'{metric}-0001.md'
                    text=file.read_text();changed=text.replace(old,new,1)
                    self.assertNotEqual(text,changed)
                    file.write_text(changed)
                    try:
                        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(ToolError,'coverage point'):
                            verify(stage)
                    finally:file.write_bytes(original[file])
            data['instances'][0]['metrics']['line']['target_sequence_sha256']='0'*64
            write_json(stage/'instance_summary.json',data)
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(ToolError,'Page digest differs'):
                verify(stage)

    def test_verifier_rejects_duplicate_point_and_broken_navigation(self):
        from scripts.check_instances import verify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            session=Session(root,{'top.a':[(0,1,0)]*2})
            second=session.roots['top.a'].child_handles()[1]
            values=list(second.values);values[6]=4;second.values=tuple(values)
            stage,_,_=self.run_analysis(session,root)
            with redirect_stdout(io.StringIO()):stats=write_report(stage,{})
            manifest=json.loads((stage/'analysis_manifest.json').read_text())
            manifest['report']=stats;write_json(stage/'analysis_manifest.json',manifest)
            with redirect_stdout(io.StringIO()):self.assertTrue(verify(stage)['passed'])
            page=stage/instance_directory('top.a')/'line-0001.md'
            original=page.read_text()
            page.write_text(original.replace('| same.sv | 3 |','| same.sv | 4 |'))
            with redirect_stdout(io.StringIO()),self.assertRaises(ToolError):verify(stage)
            page.write_text(original.replace('(index.md)','(missing.md)'))
            with redirect_stdout(io.StringIO()),self.assertRaises(ToolError):verify(stage)


if __name__=='__main__':unittest.main()
