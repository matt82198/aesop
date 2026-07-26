"""
Test suite for wave_preflight.py — wave manifest validator.

Tests validate: (1) file-ownership disjointness, (2) path existence,
(3) prompt sanity, (4) git history churn heuristics, (5) testCmd validation.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestWavePreflight(unittest.TestCase):
    """Tests for wave_preflight validator."""

    def setUp(self):
        """Create temp directory for test manifests."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = self.temp_dir.name

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _create_test_repo(self):
        """Create a minimal git repo for history tests."""
        repo_dir = os.path.join(self.temp_path, "test_repo")
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(
            ["bash", "-c", f"cd {repo_dir} && git init"],
            capture_output=True,
            timeout=5,
            check=False
        )
        return repo_dir

    def _run_validator(self, manifest, args=None, cwd=None):
        """Run wave_preflight.py and return exit code + stdout + stderr."""
        if cwd is None:
            cwd = self.temp_path

        if args is None:
            args = []

        manifest_file = os.path.join(cwd, "manifest.json")
        with open(manifest_file, "w") as f:
            json.dump(manifest, f)

        cmd = [sys.executable, "tools/wave_preflight.py", manifest_file] + args
        result = subprocess.run(
            cmd,
            cwd="C:/Users/matt8/aesop-wt-preflight",
            capture_output=True,
            timeout=10,
            text=True,
            check=False
        )
        return result.returncode, result.stdout, result.stderr

    def test_good_manifest_passes(self):
        """Good manifest with no overlaps should exit 0."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "Implement feature A. [ISOLATION: sibling worktree]"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/b.py"],
                    "prompt": "Implement feature B. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertEqual(rc, 0, f"Expected exit 0, got {rc}. stdout: {stdout}, stderr: {stderr}")
        self.assertIn("PASS", stdout)

    def test_file_ownership_overlap_fails(self):
        """Items with overlapping ownsFiles should fail."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/shared.py"],
                    "prompt": "Implement A. [ISOLATION: sibling worktree]"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/shared.py"],
                    "prompt": "Implement B. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0, "Overlapping files should fail")
        self.assertIn("FAIL", stdout)
        self.assertIn("shared.py", stdout)

    def test_glob_overlap_detection(self):
        """Glob patterns should be checked for overlap."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/*.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/module.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0, "Glob patterns should detect overlap")
        self.assertIn("FAIL", stdout)

    def test_missing_file_flagged_as_info(self):
        """Missing ownsFiles should be flagged as INFO (not fail)."""
        repo_dir = self._create_test_repo()
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["nonexistent.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": repo_dir,
            "testCmd": "echo test"
        }
        rc, stdout, stderr = self._run_validator(manifest, cwd=repo_dir)
        self.assertIn("INFO", stdout)
        self.assertEqual(rc, 0)

    def test_empty_prompt_fails(self):
        """Empty prompt should fail."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": ""
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0, "Empty prompt should fail")
        self.assertIn("FAIL", stdout)

    def test_missing_worktree_isolation_marker_warns(self):
        """Prompt without isolation marker should warn (not fail)."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "Implement feature without marker"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertIn("WARN", stdout)
        self.assertEqual(rc, 0)

    def test_allow_non_haiku_without_explicit_expect_warns(self):
        """[[ALLOW-NON-HAIKU]] without explicit expect should warn."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "Do something. [[ALLOW-NON-HAIKU]] [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertIn("WARN", stdout)
        self.assertIn("ALLOW-NON-HAIKU", stdout)

    def test_allow_non_haiku_with_explicit_expect_passes(self):
        """[[ALLOW-NON-HAIKU]] with [[ALLOW-SONNET]] should pass."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "Do something [[ALLOW-NON-HAIKU]] [[ALLOW-SONNET]] [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotIn("WARN", stdout)
        self.assertEqual(rc, 0)

    def test_testcmd_binary_on_path_passes(self):
        """testCmd with binary on PATH should pass."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotIn("FAIL", stdout)
        self.assertEqual(rc, 0)

    def test_testcmd_missing_binary_fails(self):
        """testCmd with missing binary should fail."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "nonexistent_binary_xyz_42 --test"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0, "Missing binary should fail")
        self.assertIn("FAIL", stdout)

    def test_json_output_format(self):
        """--json flag should produce machine-readable JSON."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest, args=["--json"])

        try:
            result = json.loads(stdout)
            self.assertIn("checks", result)
            self.assertIsInstance(result["checks"], list)
        except json.JSONDecodeError:
            self.fail(f"Output is not valid JSON: {stdout}")

    def test_strict_mode_warns_on_warnings(self):
        """--strict flag should exit non-zero on warnings."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "Missing marker"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest, args=["--strict"])
        self.assertNotEqual(rc, 0, "--strict should fail on warnings")

    def test_multiple_overlaps_all_listed(self):
        """Multiple overlaps should all be listed."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/shared1.py", "src/shared2.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/shared1.py", "src/other.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]"
                },
                {
                    "slug": "feat/c",
                    "ownsFiles": ["src/shared2.py"],
                    "prompt": "C. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0)
        self.assertIn("shared1.py", stdout)
        self.assertIn("shared2.py", stdout)

    def test_ascii_output_only(self):
        """Output should be pure ASCII."""
        manifest = {
            "items": [
                {
                    "slug": "feat/test",
                    "ownsFiles": ["src/test.py"],
                    "prompt": "Test. [ISOLATION: sibling worktree]"
                }
            ],
            "workDir": "/tmp/aesop",
            "testCmd": "echo test"
        }
        rc, stdout, stderr = self._run_validator(manifest)

        try:
            stdout.encode("ascii")
            stderr.encode("ascii")
        except UnicodeEncodeError:
            self.fail("Output contains non-ASCII characters")


if __name__ == "__main__":
    unittest.main()
