#!/usr/bin/env python3
"""Measure individual resolution versus a prefix trie on real NPI targets."""
import argparse
from collections import Counter
from itertools import islice
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import fingerprint, read_targets, require, write_json
from uncover_tool.native import NativeSession


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vdb', type=Path, required=True)
    p.add_argument('--instance', required=True)
    p.add_argument('--metric', default='tgl')
    p.add_argument('--tests', nargs='+')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--verdi-home', default='/nfs/tools/synopsys/verdi/U-2023.03')
    p.add_argument('--repeats', type=int, default=9)
    p.add_argument('--iterations', type=int, default=100)
    args = p.parse_args()
    require(args.repeats > 0 and args.iterations > 0, 'Repeat counts must be positive')
    require(not args.out.exists(), 'Choose a fresh output file')
    original = args.vdb.resolve()
    before = fingerprint(original)
    evidence = {'vdb': str(original), 'fingerprint': before, 'instance': args.instance,
                'metric': args.metric, 'repeats': args.repeats, 'iterations': args.iterations,
                'load_average_before': os.getloadavg(), 'cases': []}
    with tempfile.TemporaryDirectory(prefix='uncover-locator-', dir='/tmp') as temp:
        work = Path(temp)
        copied = work / 'input.vdb'
        shutil.copytree(original, copied)
        config = {'verdi_home': args.verdi_home, 'tests': args.tests, 'all_tests': False,
                  'exclude_files': [], 'instance_roots': [args.instance]}
        previous = Path.cwd()
        try:
            os.chdir(work)
            with NativeSession(config, copied) as session:
                rows = session.bins(args.instance, args.metric)
                try:
                    # Keep only ordinary descriptors after releasing the traversal.
                    trails = [list(trail) for _, trail, _ in islice(rows, 16)]
                finally:
                    rows.close()
                require(len(trails) >= 4, 'Need at least four native targets')
                for count in (1, 2, 4, 16):
                    if count > len(trails):
                        continue
                    targets = dict(enumerate(trails[:count]))
                    results, timings, counters = {}, {'single': [], 'batch': []}, {}
                    for repeat in range(args.repeats):
                        order = ('single', 'batch') if repeat % 2 == 0 else ('batch', 'single')
                        for strategy in order:
                            root = session.handle(args.instance, args.metric)
                            tick = time.perf_counter()
                            for _ in range(args.iterations):
                                values = read_targets(root, targets, session.test, session.cov.release_handle,
                                                      strategy=strategy)
                            timings[strategy].append((time.perf_counter() - tick) / args.iterations)
                            results[strategy] = values
                    require(results['single'] == results['batch'], 'Locator strategies changed native states')
                    for strategy in ('single', 'batch'):
                        calls = Counter()
                        read_targets(session.handle(args.instance, args.metric), targets, session.test,
                                     session.cov.release_handle, calls, strategy=strategy)
                        require(calls['returned_child_handles'] == calls['released_child_handles'], 'Handle leak')
                        counters[strategy] = dict(calls)
                    evidence['cases'].append({'targets': count, 'seconds_per_call': timings,
                        'median_seconds': {s: statistics.median(v) for s, v in timings.items()},
                        'native_calls': counters, 'results_equivalent': True})
        finally:
            os.chdir(previous)
    evidence['original_vdb_unchanged'] = fingerprint(original) == before
    require(evidence['original_vdb_unchanged'], 'Original VDB changed')
    evidence['load_average_after'] = os.getloadavg()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, evidence)
    for case in evidence['cases']:
        print(case['targets'], case['median_seconds'], flush=True)


if __name__ == '__main__':
    main()
