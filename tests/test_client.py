import unittest
from pathlib import Path
from replik_monitor.client import parse_changes


class SoapFixtureTests(unittest.TestCase):
    def test_historical_fixture_parses_stable_source_ids(self):
        changes = parse_changes(Path("fixtures/historical.xml").read_bytes(), "47251301")
        self.assertEqual(["R-100", "R-101"], [c.source_id for c in changes])
        self.assertEqual("https://example.test/new", changes[1].url)


if __name__ == "__main__": unittest.main()
