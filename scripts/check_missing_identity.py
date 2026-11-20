#!/usr/bin/env python3
"""Use real NPI coverage with optional XML identity hints disabled in the harness."""
import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vdb', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, default=ROOT)
    p.add_argument('--tests', nargs='+', default=['mode0'])
    p.add_argument('--mode', choices=['none', 'selected', 'all'], help=argparse.SUPPRESS)
    args = p.parse_args()
    sys.path.insert(0, str(args.source_dir.resolve()))
    from uncover_tool.common import fingerprint, require, write_json
    before = fingerprint(args.vdb.resolve())
    require(not args.out.exists(), 'Choose a fresh output directory')
    if args.mode:
        # Only this disposable worker changes hint discovery. Coverage data,
        # the VDB, native locators and native state reads remain unmodified.
        from uncover_tool import cli, worker
        config = cli.configuration(cli.parser().parse_args([
            '--vdb', str(args.vdb.resolve()), '--out', str(args.out.resolve()),
            '--tests', *args.tests, '--compare-peers', args.mode,
            '--metrics', 'line', 'tgl', 'branch', 'cond']))
        args.out.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix='uncover-no-identity-', dir='/tmp') as temp:
            config['scratch_dir'] = temp
            with patch('uncover_tool.native.xml_hints', return_value=(
                    {}, {}, ['XML identity hints disabled by native regression harness'])):
                worker.run(config, args.out.resolve())
        return
    args.out.mkdir(parents=True)
    for mode in ('none', 'selected', 'all'):
        command = [sys.executable, str(Path(__file__).resolve()), '--vdb', str(args.vdb.resolve()),
                   '--out', str((args.out / mode).resolve()), '--source-dir', str(args.source_dir.resolve()),
                   '--tests', *args.tests, '--mode', mode]
        with (args.out / (mode + '.log')).open('w') as log:
            run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        require(run.returncode == 0, f'Missing identity scenario failed: {mode}')
    records = {mode: json.loads((args.out / mode / 'uncovered_coverage.json').read_text())['records']
               for mode in ('none', 'selected', 'all')}
    facts = lambda rows: [{k: v for k, v in row.items() if k != 'same_rtl_coverage'} for row in rows]
    require(facts(records['none']) == facts(records['selected']) == facts(records['all']),
            'Identity fallback or comparison mode changed atomic facts or order')
    for mode in ('selected', 'all'):
        for row in records[mode]:
            peers = row['same_rtl_coverage']
            require(peers['comparable_instance_count'] == 1 and
                    peers['instances'][0]['instance_path'] == row['instance_path'],
                    'Unknown identity shared facts across instances')
        require(any(row['same_rtl_coverage']['incomparable_instances'] for row in records[mode]),
                'Expected unknown-identity peers to be recorded as incomparable')
    with gzip.open(args.out / 'all/same_rtl_comparison.jsonl.gz', 'rt') as stream:
        all_rows = [json.loads(line) for line in stream]
    require(all_rows and all(row['same_rtl_coverage']['comparable_instance_count'] == 1 for row in all_rows),
            'Full comparison merged unknown instances')
    unchanged = fingerprint(args.vdb.resolve()) == before
    require(unchanged, 'Identity regression changed original VDB')
    write_json(args.out / 'identity_results.json', {'passed': True, 'modes': list(records),
        'original_vdb_unchanged': unchanged, 'fingerprint': before,
        'source_dir': str(args.source_dir.resolve()), 'coverage_reader': 'real Verdi NPI',
        'hint_injection': 'xml_hints disabled in isolated test workers; VDB metadata unchanged',
        'output_records': len(records['all']), 'all_comparison_records': len(all_rows)})
    print(json.dumps({'passed': True, 'out': str(args.out)}))


if __name__ == '__main__':
    main()
