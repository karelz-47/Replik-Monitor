import re
import tarfile
import unittest
from pathlib import Path


class ReleaseArchiveTests(unittest.TestCase):
    def test_final_archive_contains_runtime_and_lifecycle_tests_without_values(self):
        archive = Path("replik-monitor-railway-0.2.1.tar.gz")
        self.assertTrue(archive.is_file(), "build the release archive before archive-level verification")
        required = {
            "Dockerfile", "README.md", "railway.toml", "requirements.txt",
            "replik_monitor/db.py", "replik_monitor/http.py", "replik_monitor/service.py",
            "tests/test_monitor.py",
        }
        forbidden = re.compile(rb"(?:sk_live_|re_[A-Za-z0-9]{8,}|@novis|recipient@example\.com)")
        with tarfile.open(archive, "r:gz") as bundle:
            names = {member.name.rstrip("/").removeprefix("./") for member in bundle.getmembers()}
            self.assertFalse(required - names, f"missing archive members: {required - names}")
            for member in bundle.getmembers():
                if member.isfile() and not member.name.removeprefix("./").startswith("tests/"):
                    self.assertIsNone(forbidden.search(bundle.extractfile(member).read()), member.name)
