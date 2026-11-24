"""Result verification must detect truncated and different fact streams."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_instances import equal_rows, stream_records
from uncover_tool.common import ToolError


class StreamingRecordsTests(unittest.TestCase):
    def test_empty_and_unicode_records_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            for suffix in ('.jsonl', '.jsonl.gz'):
                path = Path(temp) / ('records' + suffix)
                for rows in ([], [{'text': '源码\n]},', 'value': None}, {'nested': {'n': 1}}]):
                    opener = gzip.open if suffix.endswith('.gz') else Path.open
                    with opener(path, 'wt', encoding='utf-8') as stream:
                        for row in rows:
                            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                    self.assertEqual(list(stream_records(path)), rows)

    def test_missing_record_and_truncated_json_are_errors(self):
        with self.assertRaises(ToolError):
            equal_rows(iter([{'id': 1}]), iter([]), 'test')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'records.jsonl'
            path.write_text('{"id":1}\n{"id":')
            with self.assertRaises(ValueError):
                list(stream_records(path))


if __name__ == '__main__':
    unittest.main()
