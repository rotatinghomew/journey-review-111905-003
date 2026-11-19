import csv
import unittest
from pathlib import Path


class FixtureTests(unittest.TestCase):
    def test_operations_include_multiple_observation_types(self):
        path = Path(__file__).parents[1] / "fixtures" / "operations.csv"
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        kinds = {row["record_type"] for row in rows}
        self.assertGreaterEqual(len(rows), 10)
        self.assertTrue({"intake", "backlog", "shift", "absence", "skill"} <= kinds)
        self.assertTrue(all(row["row_id"] and row["date"] for row in rows))


if __name__ == "__main__":
    unittest.main()
