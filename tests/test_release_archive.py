import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


class ReleaseArchiveTests(unittest.TestCase):
    def test_final_archive_contains_runtime_and_lifecycle_tests_without_values(self):
        archive = Path("replik-monitor-railway-0.2.1.tar.gz")
        self.assertTrue(archive.is_file(), "build the release archive before archive-level verification")
        required = {
            "Dockerfile", "README.md", "railway.toml", "requirements.txt",
            "replik_monitor/db.py", "replik_monitor/http.py", "replik_monitor/service.py", "replik_monitor/cli.py",
            "tests/test_monitor.py", "tests/test_cli.py",
        }
        forbidden = re.compile(rb"(?:sk_live_|re_[A-Za-z0-9]{8,}|@novis|recipient@example\.com)")
        with tarfile.open(archive, "r:gz") as bundle:
            names = {member.name.rstrip("/").removeprefix("./") for member in bundle.getmembers()}
            self.assertFalse(required - names, f"missing archive members: {required - names}")
            for member in bundle.getmembers():
                if member.isfile() and not member.name.removeprefix("./").startswith("tests/"):
                    self.assertIsNone(forbidden.search(bundle.extractfile(member).read()), member.name)

    def test_extracted_archive_imports_cli_with_runtime_dependencies(self):
        archive = Path("replik-monitor-railway-0.2.1.tar.gz")
        with tempfile.TemporaryDirectory() as destination:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(destination, filter="data")
            result = subprocess.run(
                [sys.executable, "-m", "replik_monitor.cli", "--help"], cwd=destination,
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("{migrate,poll,deliver,serve}", result.stdout)
