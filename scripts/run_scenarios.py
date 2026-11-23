#!/usr/bin/env python3
"""Native end-to-end checks for the single coverage-facts pipeline."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import require, write_json, fingerprint
from uncover_tool.differences import FactsReader
from scripts.check_differences import compare, stream_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vdb', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path, help='pre-change full/merged/scoped result directories')
    parser.add_argument('--exclude-file', type=Path, help='existing exclusions for this fixture')
    parser.add_argument('--verdi-home', default='/nfs/tools/synopsys/verdi/U-2023.03')
    parser.add_argument('--urg-bin', default='/nfs/tools/synopsys/vcs/U-2023.03/bin/urg')
    args = parser.parse_args()
    vdb, out = args.vdb.resolve(), args.out.resolve()
    require(not out.exists(), 'Choose a new scenario output directory')
    out.mkdir(parents=True)
    before, evidence, checks = fingerprint(vdb), [], {}

    def execute(name, extra=(), test_args=('--tests', 'mode0'), expected_code=0):
        command = [sys.executable, str(ROOT / 'uncover_extract.py'), '--vdb', str(vdb), '--out', str(out / name),
                   '--verdi-home', args.verdi_home, '--metrics', 'line', 'tgl', 'branch', 'cond', *test_args, *extra]
        tick = time.perf_counter()
        with (out / (name + '.log')).open('a') as log:
            run = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        require(run.returncode == expected_code, f'Scenario {name} returned {run.returncode}; inspect its log')
        evidence.append({'case': name, 'returncode': run.returncode, 'seconds': time.perf_counter() - tick, 'command': command})
        if expected_code:
            return None
        result = out / name
        manifest = json.loads((result / 'analysis_manifest.json').read_text())
        require(manifest['schema_version'] == '2.0' and 'mode' not in manifest['configuration'], 'Unexpected default contract')
        require(not (result / 'uncovered_coverage.json').exists(), 'Legacy main records were emitted')
        require('comparison: selected ' not in (result / 'execution.log').read_text(), 'Legacy selected stage ran')
        reader = FactsReader(result / 'coverage_facts.sqlite')
        try:
            details, holes = list(reader.details()), list(reader.details('holes'))
            require(reader.db.execute('SELECT COUNT(*) FROM states WHERE (raw & 68) != 0').fetchone()[0] == 0,
                    'Excluded points entered the published facts')
        finally:
            reader.close()
        require(len(holes) == sum(s['uncovered_candidates'] for s in manifest['summary'].values()), 'Offline holes are incomplete')
        return manifest, details, holes

    urg = ['--validate-urg', '--urg-bin', args.urg_bin]
    base, details, holes = execute('full', urg)
    require(any(r['same_rtl_coverage']['incomparable_instances'] for r in details), 'Incompatible models were not reported')
    require(base['urg_validation']['passed'], 'URG verification failed')
    merged, _, _ = execute('merged', urg, ['--all-tests'])
    require(any(merged['summary'][m]['native_covered'] > base['summary'][m]['native_covered'] for m in base['summary']),
            'Native test merge added no coverage')
    require(merged['same_rtl_comparison']['targets_with_state_differences'] > 0, 'Instance differences not detected')
    _, scoped_details, scoped_holes = execute('scoped', ['--instance-root', 'fixture_tb.left'])
    require(all(r['instance_path'] == 'fixture_tb.left' for r in scoped_holes), 'Out-of-scope holes were exported')
    require(any(p['instance_path'] == 'fixture_tb.right' for r in scoped_details for p in r['same_rtl_coverage']['instances']),
            'Compatible peers outside roots were dropped')
    if args.reference:
        for name in ('full', 'merged', 'scoped'):
            checks[name] = compare(args.reference.resolve() / name, out / name)
    for size in (1, 500):
        name = f'batch-{size}'
        execute(name, ['--batch-size', str(size)])
        checks[name] = compare(out / 'full', out / name)
    empty_rtl = out / 'empty-rtl'; empty_rtl.mkdir()
    source = holes[0]['source_file']
    _, _, missing_holes = execute('missing-source', ['--path-map', str(Path(source).parent), str(empty_rtl)])
    require([r['target_id'] for r in missing_holes] == [r['target_id'] for r in holes], 'Missing source changed facts')
    require(all(r['source_mapping']['status'] == 'missing' for r in missing_holes), 'Missing source resolved unexpectedly')
    if args.exclude_file:
        excluded, _, excluded_holes = execute('existing', ['--exclude-file', str(args.exclude_file.resolve()), *urg])
        require(len(excluded_holes) < len(holes), 'Existing exclusions did not remove ordinary holes')
        reader = FactsReader(out / 'existing/coverage_facts.sqlite')
        try:
            require(reader.db.execute('SELECT COUNT(*) FROM states WHERE (raw & 68) != 0').fetchone()[0] == 0,
                    'Excluded points entered the published facts')
        finally:
            reader.close()
    for kind in ('holes', 'differences', 'common-uncovered'):
        destination = out / f'{kind}.jsonl.gz'
        subprocess.run([sys.executable, '-m', 'uncover_tool.export_facts', '--result', str(out / 'full'),
                        '--kind', kind, '--out', str(destination)], cwd=ROOT, check=True, timeout=30)
        exported = list(stream_records(destination))
        if kind == 'holes':
            require(exported == holes, 'Offline holes CLI changed facts or identities')
        elif kind == 'differences':
            require(exported == details, 'Offline difference details changed')
        checks[f'export-{kind}'] = {'records': len(exported), 'passed': True}
    execute('ambiguous-tests', test_args=[], expected_code=1)
    execute('wrong-test', test_args=['--tests', 'not-a-test'], expected_code=1)
    execute('wrong-instance', ['--instance-root', 'no.such.instance'], expected_code=1)
    old = (out / 'full/analysis_manifest.json').read_bytes()
    execute('full', ['--overwrite', '--verdi-home', str(out / 'no-verdi')], expected_code=1)
    require((out / 'full/analysis_manifest.json').read_bytes() == old, 'Failed overwrite changed published results')
    require(fingerprint(vdb) == before, 'Integration checks changed the original fixture VDB')
    write_json(out / 'scenario_results.json', {'passed': True, 'cases': evidence, 'checks': checks,
               'original_vdb_unchanged': True, 'fixture_origin': 'controlled VCS design'})
    print(json.dumps({'passed': True, 'cases': len(evidence), 'out': str(out)}))


if __name__ == '__main__':
    main()
