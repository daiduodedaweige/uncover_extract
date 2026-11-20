"""Large-output equivalence checks must detect missing records and bad tails."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.benchmark_comparison import stream_records
from uncover_tool.output import json_rows


class StreamingRecordsTests(unittest.TestCase):
    def test_empty_and_unicode_records_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'records.json'
            for rows in ([], [{'text': '源码\n]},', 'value': None}, {'nested': {'n': 1}}]):
                json_rows(path, iter(rows))
                self.assertEqual(list(stream_records(path)), json.loads(path.read_text())['records'])

    def test_missing_footer_is_an_error_after_valid_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'records.json'
            json_rows(path, [{'target': 'first'}, {'target': 'last'}])
            path.write_text(path.read_text().removesuffix('\n]}\n'))
            with self.assertRaisesRegex(ValueError, 'Incomplete records array'):
                list(stream_records(path))


if __name__ == '__main__':
    unittest.main()
