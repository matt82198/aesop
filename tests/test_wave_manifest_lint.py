"""
Test suite for wave_manifest_lint.py — wave manifest validator.

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

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
from unittest import mock


class TestWavePreflight(unittest.TestCase):
    """Tests for wave_manifest_lint validator."""

    def setUp(self):
        """Create temp directory for test manifests."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = self.temp_dir.name

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _create_test_repo(self, dirname="test_repo"):
        """Create a minimal git repo for history tests.

        List-form argv + cwd= (never `bash -c "cd {path} && ..."`): an
        interpolated path containing a space -- or a Windows backslash --
        silently corrupts a shell string.
        """
        repo_dir = os.path.join(self.temp_path, dirname)
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=repo_dir,
            capture_output=True,
            timeout=30,
            check=False
        )
        return repo_dir

    def _commit(self, repo_dir, rel_path, content):
        """Write + commit a file in repo_dir with an inline (never global) identity."""
        full = Path(repo_dir) / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", rel_path],
            cwd=repo_dir, capture_output=True, timeout=30, check=False
        )
        subprocess.run(
            [
                "git",
                "-c", "user.name=Aesop Test",
                "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", f"touch {rel_path}",
            ],
            cwd=repo_dir, capture_output=True, timeout=30, check=False
        )

    def _run_validator(self, manifest, args=None, cwd=None):
        """Run wave_manifest_lint.py and return exit code + stdout + stderr."""
        if cwd is None:
            cwd = self.temp_path

        if args is None:
            args = []

        manifest_file = os.path.join(cwd, "manifest.json")
        with open(manifest_file, "w") as f:
            json.dump(manifest, f)

        cmd = [sys.executable, "tools/wave_manifest_lint.py", manifest_file] + args
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
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
                    "prompt": "Implement feature A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/b.py"],
                    "prompt": "Implement feature B. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Implement A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/shared.py"],
                    "prompt": "Implement B. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/module.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": repo_dir
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
                    "prompt": "",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Implement feature without marker",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Do something. [[ALLOW-NON-HAIKU]] [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Do something [[ALLOW-NON-HAIKU]] [[ALLOW-SONNET]] [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "nonexistent_binary_xyz_42 --test"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Missing marker",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/shared1.py", "src/other.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/c",
                    "ownsFiles": ["src/shared2.py"],
                    "prompt": "C. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
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
                    "prompt": "Test. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": "/tmp/aesop"
        }
        rc, stdout, stderr = self._run_validator(manifest)

        try:
            stdout.encode("ascii")
            stderr.encode("ascii")
        except UnicodeEncodeError:
            self.fail("Output contains non-ASCII characters")

    # ---------------------------------------------------------------- #
    # Cluster 1: churn check must survive a repo path containing a space
    # ---------------------------------------------------------------- #

    def test_churn_check_works_in_path_with_spaces(self):
        """Churn detection must work when the repo path contains a space.

        Regression: check_git_history_churn built
        ``f"cd {repo_root} && git log ..."`` and handed it to ``bash -c``.
        A repo root containing a space (or a Windows backslash) turns the
        interpolated ``cd`` into a broken command, git never runs, and the
        check silently reports "No high-churn files detected" -- a false
        PASS. The fix is list-form argv with cwd=, no shell at all.
        """
        repo_dir = self._create_test_repo("repo with spaces")
        for i in range(5):
            self._commit(repo_dir, "src/hot.py", f"# rev {i}\n")

        manifest = {
            "items": [
                {
                    "slug": "feat/hot",
                    "ownsFiles": ["src/hot.py"],
                    "prompt": "Touch the hot file. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": repo_dir
        }
        rc, stdout, stderr = self._run_validator(
            manifest, args=["--root", repo_dir], cwd=repo_dir
        )
        self.assertIn(
            "WARN: git_history_churn", stdout,
            f"churn not detected in a spaced path. stdout: {stdout}, stderr: {stderr}"
        )
        self.assertIn("src/hot.py", stdout)
        self.assertEqual(rc, 0, "churn is a WARN, not a FAIL")

    def test_churn_check_quiet_on_cold_repo(self):
        """A repo with a single commit must not warn (no false churn)."""
        repo_dir = self._create_test_repo("cold repo with spaces")
        self._commit(repo_dir, "src/cold.py", "# once\n")

        manifest = {
            "items": [
                {
                    "slug": "feat/cold",
                    "ownsFiles": ["src/cold.py"],
                    "prompt": "Touch the cold file. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "workDir": repo_dir
        }
        rc, stdout, stderr = self._run_validator(
            manifest, args=["--root", repo_dir], cwd=repo_dir
        )
        self.assertIn("PASS: git_history_churn", stdout)
        self.assertEqual(rc, 0)

    # ---------------------------------------------------------------- #
    # Cluster 2: testCmd is a PER-ITEM field (what driver/wave_loop reads)
    # ---------------------------------------------------------------- #

    def test_shipped_example_manifest_is_per_item_shaped(self):
        """Ground truth: the shipped example manifest has no top-level testCmd.

        driver/wave_loop.py and driver/wave_scheduler.py both read
        ``item["testCmd"]``; nothing reads a top-level ``testCmd``. This test
        pins the shape the linter must validate against.
        """
        example = Path(REPO_ROOT) / "examples" / "first-wave-baseline" / "wave-manifest.json"
        data = json.loads(example.read_text(encoding="utf-8"))
        self.assertNotIn("testCmd", data, "example manifest has no top-level testCmd")
        self.assertTrue(data.get("items"))
        for item in data["items"]:
            self.assertTrue(
                item.get("testCmd"),
                f"item {item.get('slug')!r} must carry its own testCmd"
            )

    def test_strict_mode_passes_on_real_shaped_manifest(self):
        """--strict must exit 0 on a real-shaped (per-item testCmd) manifest.

        Regression: check_testcmd_validity read the TOP-LEVEL testCmd, which
        real manifests never set, so it always emitted
        "WARN: No testCmd specified" -- making --strict exit 1 on every real
        manifest, i.e. strict mode was permanently broken.

        Shape mirrors examples/first-wave-baseline/wave-manifest.json. Run
        against a cold temp repo so the other four checks are all quiet and
        the exit code isolates the signal under test (the isolation marker is
        added for the same reason).
        """
        repo_dir = self._create_test_repo("strict repo with spaces")
        self._commit(repo_dir, "README.md", "# readme\n")
        self._commit(repo_dir, "CHANGELOG.md", "# changelog\n")
        manifest = {
            "wave_id": "first-wave-baseline",
            "wave_description": "Baseline wave",
            "items": [
                {
                    "slug": "readme-typo-fix",
                    "ownsFiles": ["README.md"],
                    "prompt": "Fix the typo. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version",
                    "workDir": "."
                },
                {
                    "slug": "changelog-touch",
                    "ownsFiles": ["CHANGELOG.md"],
                    "prompt": "Add an entry. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version",
                    "workDir": "."
                }
            ]
        }
        rc, stdout, stderr = self._run_validator(
            manifest, args=["--strict", "--root", repo_dir], cwd=repo_dir
        )
        self.assertIn("PASS: testcmd_validity", stdout)
        self.assertNotIn("WARN", stdout)
        self.assertEqual(
            rc, 0,
            f"--strict must not fail a valid real-shaped manifest. stdout: {stdout}"
        )

    def test_item_missing_testcmd_warns(self):
        """An item with no testCmd should WARN (engine cannot verify it)."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/b",
                    "ownsFiles": ["src/b.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]"
                }
            ]
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertIn("WARN: testcmd_validity", stdout)
        self.assertIn("feat/b", stdout)
        self.assertEqual(rc, 0)

    def test_top_level_testcmd_is_reported_as_ignored(self):
        """A top-level testCmd must WARN: the engine never reads it."""
        manifest = {
            "items": [
                {
                    "slug": "feat/a",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                }
            ],
            "testCmd": "python --version"
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertIn("WARN: testcmd_validity", stdout)
        self.assertIn("top-level", stdout)
        self.assertEqual(rc, 0)

    def test_item_testcmd_missing_binary_fails_by_slug(self):
        """A per-item testCmd whose binary is absent should FAIL and name the item."""
        manifest = {
            "items": [
                {
                    "slug": "feat/good",
                    "ownsFiles": ["src/a.py"],
                    "prompt": "A. [ISOLATION: sibling worktree]",
                    "testCmd": "python --version"
                },
                {
                    "slug": "feat/bad",
                    "ownsFiles": ["src/b.py"],
                    "prompt": "B. [ISOLATION: sibling worktree]",
                    "testCmd": "nonexistent_binary_xyz_42 --test"
                }
            ]
        }
        rc, stdout, stderr = self._run_validator(manifest)
        self.assertNotEqual(rc, 0)
        self.assertIn("FAIL: testcmd_validity", stdout)
        self.assertIn("feat/bad", stdout)


if __name__ == "__main__":
    unittest.main()
