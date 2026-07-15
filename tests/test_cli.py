import subprocess
import sys
import unittest


class CliEntrypointTests(unittest.TestCase):
    def test_module_entrypoint_loads_runtime_dependencies_and_exposes_commands(self):
        result = subprocess.run(
            [sys.executable, "-m", "replik_monitor.cli", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("{migrate,poll,deliver,serve}", result.stdout)


if __name__ == "__main__":
    unittest.main()
