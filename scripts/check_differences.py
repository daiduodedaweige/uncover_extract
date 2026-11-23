#!/usr/bin/env python3
"""Verify retained native facts and grouped reports against a reference result."""
import argparse
import gzip
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uncover_tool.common import digest_file, require, write_json


def stream_records(path):
    opener = gzip.open if path.suffix == '.gz' else Path.open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def equal_rows(left, right, label):
    missing = object()
    for first, second in zip_longest(left, right, fillvalue=missing):
        require(first == second, f'{label} differs')


def compare(reference, result):
    reference, result = Path(reference), Path(result)
    before, after = [json.loads((p / 'analysis_manifest.json').read_text()) for p in (reference, result)]
    for field in ('input', 'selected_tests', 'available_tests', 'test_scope_ref', 'capabilities',
                  'summary', 'source_mappings', 'warnings'):
        require(before[field] == after[field], f'Manifest differs: {field}')
    # Earlier standalone reports recorded an implementation mode in this section.
    require({k: v for k, v in before['same_rtl_comparison'].items() if k != 'mode'} ==
            {k: v for k, v in after['same_rtl_comparison'].items() if k != 'mode'}, 'Comparison summary differs')
    connections = [sqlite3.connect((p / 'coverage_facts.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
                   for p in (reference, result)]
    counts = {}
    try:
        tables = [row[0] for row in connections[0].execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        require(tables == [row[0] for row in connections[1].execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")], 'Fact tables differ')
        for table in tables:
            require(table.isidentifier(), 'Unexpected facts table name')
            require(connections[0].execute(f'PRAGMA table_info({table})').fetchall() ==
                    connections[1].execute(f'PRAGMA table_info({table})').fetchall(), f'Fact columns differ: {table}')
            if table == 'metadata':
                for field in ('database_id', 'test_scope_ref', 'selected_tests', 'comparison_basis'):
                    rows = [db.execute('SELECT value FROM metadata WHERE key=?', (field,)).fetchone() for db in connections]
                    require(rows[0] == rows[1], f'Fact metadata differs: {field}')
                continue
            columns = len(connections[0].execute(f'PRAGMA table_info({table})').fetchall())
            query = f'SELECT * FROM {table} ORDER BY ' + ','.join(str(n) for n in range(1, columns + 1))
            equal_rows(*(db.execute(query) for db in connections), f'Facts/{table}')
            counts[table] = connections[1].execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    finally:
        for db in connections:
            db.close()
    for name in ('differences.jsonl.gz', 'difference_patterns.jsonl.gz'):
        equal_rows(stream_records(reference / name), stream_records(result / name), name)
    for name in ('module_summary.json', 'differences.csv', 'report.md'):
        require(digest_file(reference / name) == digest_file(result / name), f'Report differs: {name}')
    return {'equal': True, 'table_counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--out', type=Path, help='new verification JSON file')
    args = parser.parse_args()
    if args.out:
        require(not args.out.exists(), 'Choose a new verification output file')
    result = compare(args.reference, args.result)
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
