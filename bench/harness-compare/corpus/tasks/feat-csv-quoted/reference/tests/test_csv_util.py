import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csv_util import csv_row  # noqa: E402


class CsvRowTests(unittest.TestCase):
    def test_quoted_comma(self):
        self.assertEqual(csv_row('a,"b,c",d'), ["a", "b,c", "d"])

    def test_doubled_quote(self):
        self.assertEqual(csv_row('"x""y",z'), ['x"y', "z"])

    def test_plain(self):
        self.assertEqual(csv_row("a,b"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
