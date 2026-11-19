import csv
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEADERS = {'windows': ['id', 'available_from', 'available_to', 'services'], 'releases': ['window', 'effective', 'page'], 'orders': ['id', 'window', 'step', 'page', 'status', 'reviewed'], 'limits': ['min_per_path', 'min_change', 'maturity_days'], 'episodes': ['id', 'channel', 'window', 'topic', 'started'], 'events': ['id', 'episode', 'time', 'step', 'page', 'kind', 'detail'], 'links': ['id', 'from_episode', 'to_episode', 'evidence', 'state', 'recorded', 'replaces'], 'revision_relationships': ['from_order', 'to_order', 'kind'], 'reviews': ['id', 'as_of', 'orders', 'decisions']}
LINKS = [('episodes', 'window', 'windows', 'id'), ('events', 'episode', 'episodes', 'id'), ('links', 'from_episode', 'episodes', 'id'), ('links', 'to_episode', 'episodes', 'id')]


def read(name):
    with (FIXTURES / (name + ".csv")).open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames, list(reader)


class SourceRecords(unittest.TestCase):
    def test_records_match_source_columns(self):
        for name, expected in HEADERS.items():
            with self.subTest(table=name):
                header, rows = read(name)
                self.assertEqual(header, expected)
                self.assertTrue(rows)
                self.assertTrue(all(None not in row and None not in row.values() for row in rows))
                if "id" in header and (name != "receipts" or "batch" not in header):
                    ids = [row["id"] for row in rows]
                    self.assertTrue(all(ids))
                    self.assertEqual(len(ids), len(set(ids)))

    def test_record_references_exist(self):
        for source, field, target, key in LINKS:
            with self.subTest(table=source, column=field):
                _, source_rows = read(source)
                _, target_rows = read(target)
                keys = {row[key] for row in target_rows}
                self.assertTrue(all(row[field] in keys for row in source_rows))
