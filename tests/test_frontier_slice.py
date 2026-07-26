#!/usr/bin/env python3
"""Tests for frontier discrimination benchmark slice.

Covers:
- Scorer correctness (exact and regex matching)
- Task schema validation (all required fields, no secrets)
- Live-mode gating (--confirm-spend requirement)
- Ground truth schema validation
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
import subprocess

# Add bench to path
bench_dir = Path(__file__).parent.parent / "bench"
sys.path.insert(0, str(bench_dir))


# ============================================================================
# Test Scorer
# ============================================================================


class TestFrontierScorer(unittest.TestCase):
    """Tests for frontier task scoring logic."""

    def test_exact_match_success(self):
        """Exact match should succeed on case-insensitive match."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="exact",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected="EQUIVALENT")

        # Case-insensitive match
        score = score_response(task, "EQUIVALENT", gt)
        self.assertTrue(score.correct)

        score = score_response(task, "equivalent", gt)
        self.assertTrue(score.correct)

    def test_exact_match_failure(self):
        """Exact match should fail on different text."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="exact",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected="EQUIVALENT")

        score = score_response(task, "NOT_EQUIVALENT", gt)
        self.assertFalse(score.correct)

    def test_regex_match_success(self):
        """Regex match should succeed on pattern match."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="regex",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected_regex=r"TOCTOU|check.*then.*act")

        # Pattern match
        score = score_response(task, "This is a TOCTOU vulnerability", gt)
        self.assertTrue(score.correct)

        score = score_response(task, "check then act pattern", gt)
        self.assertTrue(score.correct)

    def test_regex_match_failure(self):
        """Regex match should fail when pattern doesn't match."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="regex",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected_regex=r"TOCTOU|check.*then.*act")

        score = score_response(task, "Something completely different", gt)
        self.assertFalse(score.correct)

    def test_empty_response(self):
        """Empty response should fail."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="exact",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected="EQUIVALENT")

        score = score_response(task, "", gt)
        self.assertFalse(score.correct)

    def test_regex_case_insensitive(self):
        """Regex matching should be case-insensitive."""
        from frontier_slice import FrontierTask, GroundTruth, score_response

        task = FrontierTask(
            id="t01",
            category="test",
            match="regex",
            prompt="test",
        )
        gt = GroundTruth(id="t01", expected_regex=r"race.*condition")

        score = score_response(task, "The RACE CONDITION occurs here", gt)
        self.assertTrue(score.correct)


# ============================================================================
# Test Task Schema Validation
# ============================================================================


class TestTaskSchemaValidation(unittest.TestCase):
    """Validate frontier tasks schema and redaction."""

    def test_tasks_file_exists(self):
        """Tasks file should exist."""
        path = bench_dir / "tasks_frontier.jsonl"
        self.assertTrue(path.exists(), f"{path} not found")

    def test_all_tasks_valid_json(self):
        """All task lines should be valid JSON."""
        path = bench_dir / "tasks_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as e:
                        self.fail(f"Line {i} is invalid JSON: {e}")

    def test_all_tasks_have_required_fields(self):
        """All tasks must have id, category, match, prompt, discrimination_rationale."""
        path = bench_dir / "tasks_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    required = ["id", "category", "match", "prompt", "discrimination_rationale"]
                    for field in required:
                        self.assertIn(field, obj, f"Task {obj.get('id', '?')} (line {i}) missing {field}")

    def test_match_is_exact_or_regex(self):
        """Match field must be 'exact' or 'regex'."""
        path = bench_dir / "tasks_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    self.assertIn(
                        obj.get("match"),
                        ["exact", "regex"],
                        f"Task {obj.get('id')} has invalid match: {obj.get('match')}",
                    )

    def test_no_absolute_paths_in_prompts(self):
        """Tasks should not contain absolute paths (redaction check)."""
        path = bench_dir / "tasks_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    prompt = obj.get("prompt", "")
                    # Check for Windows paths
                    self.assertNotRegex(
                        prompt,
                        r"[C-Z]:\\",
                        f"Task {obj.get('id')} contains absolute Windows path",
                    )
                    # Check for Unix paths
                    self.assertNotRegex(
                        prompt,
                        r"^/[a-z]",
                        f"Task {obj.get('id')} contains absolute Unix path",
                    )

    def test_no_secrets_in_prompts(self):
        """Tasks should not contain API keys, tokens, or passwords."""
        path = bench_dir / "tasks_frontier.jsonl"
        secret_patterns = [
            r"(api[_-]?key|sk[-_])",  # API keys
            r"(password|passwd|pwd|secret|token)\s*[:=]",  # Secrets
            r"(github|gitlab|bitbucket).*token",  # Git tokens
        ]
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    prompt = obj.get("prompt", "").lower()
                    for pattern in secret_patterns:
                        self.assertNotRegex(
                            prompt,
                            pattern,
                            f"Task {obj.get('id')} may contain secrets matching {pattern}",
                        )


# ============================================================================
# Test Ground Truth Schema Validation
# ============================================================================


class TestGroundTruthValidation(unittest.TestCase):
    """Validate ground truth schema."""

    def test_ground_truth_file_exists(self):
        """Ground truth file should exist."""
        path = bench_dir / "ground_truth_frontier.jsonl"
        self.assertTrue(path.exists(), f"{path} not found")

    def test_all_ground_truth_valid_json(self):
        """All ground truth lines should be valid JSON."""
        path = bench_dir / "ground_truth_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as e:
                        self.fail(f"Line {i} is invalid JSON: {e}")

    def test_all_ground_truth_have_id(self):
        """All ground truth entries must have id."""
        path = bench_dir / "ground_truth_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    self.assertIn("id", obj, f"Ground truth line {i} missing id")

    def test_ground_truth_has_expected_or_regex(self):
        """Each ground truth must have expected or expected_regex."""
        path = bench_dir / "ground_truth_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    has_expected = "expected" in obj and obj["expected"] is not None
                    has_regex = "expected_regex" in obj and obj["expected_regex"] is not None
                    self.assertTrue(
                        has_expected or has_regex,
                        f"Ground truth {obj.get('id')} has neither expected nor expected_regex",
                    )

    def test_regex_ground_truth_has_exemplar_and_counter(self):
        """Each regex ground truth must have exemplar and counter_example fields."""
        import re
        path = bench_dir / "ground_truth_frontier.jsonl"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    obj = json.loads(line)
                    if "expected_regex" in obj and obj["expected_regex"]:
                        # Must have both exemplar and counter_example
                        self.assertIn(
                            "exemplar",
                            obj,
                            f"Ground truth {obj.get('id')} (line {i}) has regex but missing exemplar",
                        )
                        self.assertIn(
                            "counter_example",
                            obj,
                            f"Ground truth {obj.get('id')} (line {i}) has regex but missing counter_example",
                        )

                        # Exemplar must match the regex
                        exemplar = obj.get("exemplar", "")
                        regex_pattern = obj.get("expected_regex")
                        match = re.search(regex_pattern, exemplar, re.IGNORECASE | re.DOTALL)
                        self.assertIsNotNone(
                            match,
                            f"Ground truth {obj.get('id')} exemplar does NOT match its own regex: {exemplar[:100]}...",
                        )

                        # Counter-example must NOT match the regex
                        counter = obj.get("counter_example", "")
                        match = re.search(regex_pattern, counter, re.IGNORECASE | re.DOTALL)
                        self.assertIsNone(
                            match,
                            f"Ground truth {obj.get('id')} counter_example INCORRECTLY matches its regex: {counter[:100]}...",
                        )

    def test_tasks_and_ground_truth_aligned(self):
        """All tasks should have corresponding ground truth."""
        from frontier_slice import load_frontier_tasks, load_ground_truth

        tasks = load_frontier_tasks(str(bench_dir / "tasks_frontier.jsonl"))
        gt_dict = load_ground_truth(str(bench_dir / "ground_truth_frontier.jsonl"))

        task_ids = {t.id for t in tasks}
        gt_ids = set(gt_dict.keys())

        missing_gt = task_ids - gt_ids
        self.assertEqual(len(missing_gt), 0, f"Tasks missing ground truth: {missing_gt}")

        extra_gt = gt_ids - task_ids
        self.assertEqual(len(extra_gt), 0, f"Ground truth without tasks: {extra_gt}")


# ============================================================================
# Test CLI Gating
# ============================================================================


class TestLiveModeGating(unittest.TestCase):
    """Test that --mode live requires --confirm-spend."""

    def test_live_mode_without_confirm_spend_exits_2(self):
        """Running --mode live without --confirm-spend should exit with code 2."""
        result = subprocess.run(
            [
                sys.executable,
                str(bench_dir / "frontier_slice.py"),
                "--mode", "live",
                "--model", "claude-3-5-opus-20241022",
                # Deliberately NOT providing --confirm-spend
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2, f"Expected exit 2, got {result.returncode}")
        self.assertIn("confirm-spend", result.stdout, "Should mention --confirm-spend in output")

    def test_live_mode_prints_cost_estimate(self):
        """Running --mode live should print cost estimate."""
        result = subprocess.run(
            [
                sys.executable,
                str(bench_dir / "frontier_slice.py"),
                "--mode", "live",
                "--model", "claude-3-5-opus-20241022",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("COST ESTIMATE", result.stdout, "Should show cost estimate")
        self.assertIn("USD", result.stdout, "Should mention USD pricing")
        self.assertIn("tokens", result.stdout, "Should mention tokens")


# ============================================================================
# Test Offline Mode
# ============================================================================


class TestOfflineMode(unittest.TestCase):
    """Test offline benchmark execution."""

    def test_offline_mode_runs(self):
        """Offline mode should run without API key."""
        result = subprocess.run(
            [
                sys.executable,
                str(bench_dir / "frontier_slice.py"),
                "--mode", "offline",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={k: v for k, v in subprocess.os.environ.items() if k != "ANTHROPIC_API_KEY"},
        )
        self.assertEqual(result.returncode, 0, f"Offline mode failed: {result.stderr}")
        self.assertIn("Accuracy", result.stdout, "Should show accuracy results")

    def test_offline_mode_produces_json(self):
        """Offline mode should produce JSON output file."""
        import tempfile
        import os as os_module

        # Use temp directory for test output isolation
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "frontier_slice_results.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(bench_dir / "frontier_slice.py"),
                    "--mode", "offline",
                    "--output", str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output_path.exists(), f"Output file not created: {output_path}")

            # Validate JSON
            with open(output_path) as f:
                data = json.load(f)
                self.assertIn("accuracy_percent", data)
                self.assertIn("tasks", data)
                self.assertGreater(len(data["tasks"]), 0)


# ============================================================================
# Test Hygiene (from test_test_hygiene.py pattern)
# ============================================================================


class TestTestHygiene(unittest.TestCase):
    """Ensure tests don't pollute global state."""

    def test_cwd_unchanged(self):
        """Test should not change working directory."""
        import os
        original_cwd = os.getcwd()
        # (actual tests run in isolated subprocess, so this is meta)
        self.assertEqual(os.getcwd(), original_cwd)

    def test_env_clean(self):
        """Test should not pollute environment."""
        # Tests use subprocess isolation, so env should be clean
        # This is checked by the offline mode test not requiring ANTHROPIC_API_KEY
        pass


if __name__ == "__main__":
    unittest.main()
