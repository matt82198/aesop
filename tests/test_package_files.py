#!/usr/bin/env python3
"""
Regression guard: Assert that all runtime code directories are included in package.json "files" array.

This test prevents the npm packaging bug where runtime directories are inadvertently omitted.
It verifies that:
  1. Every tracked *.py file in runtime directories has a glob pattern in package.json "files"
  2. The driver/*.py pattern is explicitly present (the bug we fixed)
"""

import json
import unittest
from pathlib import Path


class TestPackageFiles(unittest.TestCase):
    """Verify package.json includes all runtime code directories."""

    def setUp(self):
        """Load package.json and identify tracked runtime directories."""
        repo_root = Path(__file__).parent.parent
        self.package_json_path = repo_root / "package.json"

        with open(self.package_json_path, "r") as f:
            self.package_json = json.load(f)

        self.files_array = self.package_json.get("files", [])
        self.repo_root = repo_root

    def test_driver_py_present(self):
        """Explicitly assert that driver/*.py is in the files array."""
        self.assertIn("driver/*.py", self.files_array,
                      "driver/*.py must be in package.json files array (fixes npm ship bug)")

    def test_all_runtime_dirs_covered(self):
        """
        Assert that every runtime directory with tracked *.py files has a matching glob.

        Runtime directories are those that users must be able to import from.
        Excludes: bench/, tests/, and other dev-only directories.
        """
        runtime_dirs = ["driver", "state_store", "tools", "ui"]

        for dir_name in runtime_dirs:
            dir_path = self.repo_root / dir_name
            if not dir_path.exists():
                continue

            py_files = list(dir_path.glob("*.py"))
            if not py_files:
                # No Python files in this directory; skip it
                continue

            # Check if a matching pattern exists in files array
            patterns = [
                f"{dir_name}/*.py",
                f"{dir_name}/**",
                dir_name,  # catches directory-level includes
            ]

            found = False
            for pattern in patterns:
                if pattern in self.files_array:
                    found = True
                    break

            self.assertTrue(
                found,
                f"Directory '{dir_name}/' has {len(py_files)} tracked Python files "
                f"but no matching glob pattern in package.json files array. "
                f"Add one of: {patterns}"
            )

    def test_no_bench_included(self):
        """Verify that bench/ (dev-only) is NOT included in files."""
        bench_patterns = ["bench/*.py", "bench/**", "bench"]

        for pattern in bench_patterns:
            self.assertNotIn(
                pattern,
                self.files_array,
                f"bench/ should NOT be shipped (dev-only benchmark). "
                f"Pattern '{pattern}' must be removed from files array."
            )


if __name__ == "__main__":
    unittest.main()
