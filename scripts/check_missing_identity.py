#!/usr/bin/env python3
"""Verify real NPI facts with optional XML identity hints disabled."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import fingerprint, require, write_json
from uncover_tool.differences import FactsReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vdb', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tests', nargs='+', default=['mode0'])
    parser.add_argument('--worker', choices=['normal', 'no-hints'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    vdb, out = args.vdb.resolve(), args.out.resolve()
    before = fingerprint(vdb)
    require(not out.exists(), 'Choose a fresh output directory')
    out.mkdir(parents=True)
    if args.worker:
        from uncover_tool import cli, worker
        config = cli.configuration(cli.parser().parse_args([
            '--vdb', str(vdb), '--out', str(out), '--tests', *args.tests,
            '--metrics', 'line', 'tgl', 'branch', 'cond']))
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory(prefix='uncover-no-identity-') as temp:
                config['scratch_dir'] = temp
                if args.worker == 'no-hints':
                    with patch('uncover_tool.native.xml_hints', return_value=(
                            {}, {}, ['XML identity hints disabled by native regression harness'])):
                        worker.run(config, out)
                else:
                    worker.run(config, out)
        finally:
            os.chdir(previous)
        return
    records = {}
    for label in ('normal', 'no-hints'):
        command = [sys.executable, str(Path(__file__).resolve()), '--vdb', str(vdb),
                   '--out', str(out / label), '--tests', *args.tests, '--worker', label]
        with (out / (label + '.log')).open('w') as log:
            run = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        require(run.returncode == 0, f'Missing identity check failed: {label}')
        reader = FactsReader(out / label / 'coverage_facts.sqlite')
        try:
            records[label] = {r['target_id']: (r['covered'], r['coverable'], r['raw_status']) for r in reader.details('holes')}
            if label == 'no-hints':
                details = list(reader.details())
                require(details and all(r['same_rtl_coverage']['comparable_instance_count'] == 1 for r in details),
                        'Unknown models shared facts across instances')
                require(any(r['same_rtl_coverage']['incomparable_instances'] for r in details), 'Incomparable reasons are missing')
        finally:
            reader.close()
    require(records['normal'] == records['no-hints'], 'Missing identity changed original hole facts')
    require(fingerprint(vdb) == before, 'Identity checks changed original VDB')
    write_json(out / 'identity_results.json', {'passed': True, 'original_vdb_unchanged': True,
        'hole_records': len(records['normal']), 'coverage_reader': 'real Verdi NPI'})
    print(json.dumps({'passed': True, 'out': str(out)}))


if __name__ == '__main__':
    main()
