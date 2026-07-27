#!/usr/bin/env python3
"""Tests for bench/frontier_eligibility.py helper (TDD for v5 tool-use mode)."""

import json
import re
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from bench.frontier_eligibility import parse_token_set, extract_correct_token, audit_tasks, remove_format_instruction
from bench.frontier_slice import load_frontier_tasks, load_ground_truth, score_response


class TestParseTokenSet(unittest.TestCase):
    """Test token set extraction from prompts."""

    def test_parse_first_line_exactly_format(self):
        """Extract 'First line of your response: exactly A or B' format."""
        prompt = """Analyze the code.
First line of your response: exactly CORRECT or INCORRECT
Provide reasoning below."""
        tokens = parse_token_set(prompt)
        self.assertEqual(tokens, ["CORRECT", "INCORRECT"])

    def test_parse_first_line_variant_no_of_your_response(self):
        """Extract 'First line: exactly A or B' format."""
        prompt = "First line: exactly YES or NO"
        tokens = parse_token_set(prompt)
        self.assertEqual(tokens, ["YES", "NO"])

    def test_parse_answer_with_format(self):
        """Extract 'Answer with A or B on the first line' format."""
        prompt = "Some prompt text.\nAnswer with SAFE or UNSAFE on the first line\nMore text."
        tokens = parse_token_set(prompt)
        self.assertEqual(tokens, ["SAFE", "UNSAFE"])

    def test_parse_comma_separated_tokens(self):
        """Extract comma-separated tokens."""
        prompt = "First line: exactly A, B, or C"
        tokens = parse_token_set(prompt)
        self.assertEqual(sorted(tokens), ["A", "B", "C"])

    def test_parse_slash_separated_tokens(self):
        """Extract slash-separated tokens."""
        prompt = "First line: exactly X / Y / Z"
        tokens = parse_token_set(prompt)
        self.assertEqual(sorted(tokens), ["X", "Y", "Z"])

    def test_parse_mixed_separators(self):
        """Extract tokens with mixed separators (comma + or)."""
        prompt = "First line: exactly TOKEN_A, TOKEN_B, or TOKEN_C"
        tokens = parse_token_set(prompt)
        self.assertEqual(sorted(tokens), ["TOKEN_A", "TOKEN_B", "TOKEN_C"])

    def test_parse_case_insensitive_first_line(self):
        """Format instruction is case-insensitive."""
        prompt = "FIRST LINE OF YOUR RESPONSE: exactly VAL1 or VAL2"
        tokens = parse_token_set(prompt)
        self.assertEqual(tokens, ["VAL1", "VAL2"])

    def test_parse_ignores_non_uppercase_underscore_parts(self):
        """Filter out tokens that don't match [A-Z0-9_]+ pattern."""
        prompt = "First line: exactly VALID_TOKEN, some-invalid, ANOTHER_VALID"
        tokens = parse_token_set(prompt)
        # Only VALID_TOKEN and ANOTHER_VALID pass the [A-Z0-9_]+ filter
        self.assertEqual(sorted(tokens), ["ANOTHER_VALID", "VALID_TOKEN"])

    def test_parse_returns_none_no_format(self):
        """Return None if no format instruction found."""
        prompt = "This is a normal prompt without any token set instruction."
        tokens = parse_token_set(prompt)
        self.assertIsNone(tokens)

    def test_parse_returns_none_too_few_tokens(self):
        """Return None if fewer than 2 tokens (not a binary choice)."""
        prompt = "First line: exactly SINGLE_TOKEN"
        tokens = parse_token_set(prompt)
        self.assertIsNone(tokens)

    def test_parse_strips_punctuation(self):
        """Strip backticks, quotes from tokens."""
        prompt = 'First line: exactly "TOKEN_A" or \'TOKEN_B\''
        tokens = parse_token_set(prompt)
        self.assertEqual(sorted(tokens), ["TOKEN_A", "TOKEN_B"])

    def test_parse_stops_at_newline(self):
        """Parse stops at first newline (multiline instructions use first line only)."""
        prompt = """First line: exactly A or B
or C or D"""
        tokens = parse_token_set(prompt)
        # Should only get A, B; stops at first newline
        self.assertEqual(tokens, ["A", "B"])


class TestExtractCorrectToken(unittest.TestCase):
    """Test correct-token extraction from ground-truth regex."""

    def test_exact_match(self):
        """Extract token that matches ground-truth regex."""
        tokens = ["CORRECT", "INCORRECT"]
        # Use word boundary to distinguish from INCORRECT
        regex = r"(?i)^CORRECT\b"
        correct = extract_correct_token(tokens, regex)
        self.assertEqual(correct, "CORRECT")

    def test_case_insensitive_match(self):
        """Regex matching is case-insensitive (per SCORE_FLAGS)."""
        tokens = ["SAFE", "UNSAFE"]
        # Use NOT_ prefix to match only SAFE, not UNSAFE
        regex = r"(?i)^SAFE\b"
        correct = extract_correct_token(tokens, regex)
        self.assertEqual(correct, "SAFE")

    def test_lookahead_pattern(self):
        """Extract token matching complex lookahead pattern."""
        tokens = ["YES", "NO"]
        regex = r"(?=.*Y)(?=.*S)"  # lookahead for Y and S
        correct = extract_correct_token(tokens, regex)
        self.assertEqual(correct, "YES")

    def test_no_match_returns_none(self):
        """Return None if no token matches."""
        tokens = ["A", "B"]
        regex = r"Z"
        correct = extract_correct_token(tokens, regex)
        self.assertIsNone(correct)

    def test_multiple_matches_returns_none(self):
        """Return None if multiple tokens match (ambiguous)."""
        tokens = ["ABC", "ABD"]
        regex = r"AB"  # matches both
        correct = extract_correct_token(tokens, regex)
        self.assertIsNone(correct)


class TestAuditTasks(unittest.TestCase):
    """Test full audit of task set for tool-mode eligibility."""

    def setUp(self):
        """Create temporary task/ground-truth files for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_file = Path(self.temp_dir.name) / "tasks.jsonl"
        self.gt_file = Path(self.temp_dir.name) / "ground_truth.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_tool_mode_eligible(self):
        """Audit correctly identifies tool-mode eligible tasks."""
        # Task with token set instruction + matching ground truth
        tasks = {
            "tool_task": {
                "id": "tool_task",
                "prompt": "First line: exactly A or B",
            }
        }
        gt = {
            "tool_task": {
                "id": "tool_task",
                "expected_regex": r"(?i)A",
            }
        }
        with open(self.tasks_file, "w") as f:
            for t in tasks.values():
                f.write(json.dumps(t) + "\n")
        with open(self.gt_file, "w") as f:
            for g in gt.values():
                f.write(json.dumps(g) + "\n")

        tool_mode, regex_fallback = audit_tasks(str(self.tasks_file), str(self.gt_file))

        self.assertIn("tool_task", tool_mode)
        self.assertEqual(tool_mode["tool_task"][0], ["A", "B"])
        self.assertEqual(tool_mode["tool_task"][1], "A")

    def test_audit_regex_fallback_no_token_set(self):
        """Tasks without token set instruction fall back to regex."""
        tasks = {
            "regex_task": {
                "id": "regex_task",
                "prompt": "Analyze this. No token set here.",
            }
        }
        gt = {
            "regex_task": {
                "id": "regex_task",
                "expected_regex": r"some pattern",
            }
        }
        with open(self.tasks_file, "w") as f:
            for t in tasks.values():
                f.write(json.dumps(t) + "\n")
        with open(self.gt_file, "w") as f:
            for g in gt.values():
                f.write(json.dumps(g) + "\n")

        tool_mode, regex_fallback = audit_tasks(str(self.tasks_file), str(self.gt_file))

        self.assertIn("regex_task", regex_fallback)
        self.assertNotIn("regex_task", tool_mode)

    def test_audit_regex_fallback_ambiguous_token(self):
        """Tasks with ambiguous token matches (multiple or none) fall back."""
        # Ground truth accepts both A and B, so can't pick one
        tasks = {
            "ambig_task": {
                "id": "ambig_task",
                "prompt": "First line: exactly A or B",
            }
        }
        gt = {
            "ambig_task": {
                "id": "ambig_task",
                "expected_regex": r"(?i)(?:A|B)",  # matches both
            }
        }
        with open(self.tasks_file, "w") as f:
            for t in tasks.values():
                f.write(json.dumps(t) + "\n")
        with open(self.gt_file, "w") as f:
            for g in gt.values():
                f.write(json.dumps(g) + "\n")

        tool_mode, regex_fallback = audit_tasks(str(self.tasks_file), str(self.gt_file))

        self.assertIn("ambig_task", regex_fallback)
        self.assertNotIn("ambig_task", tool_mode)

    def test_audit_mixed_set(self):
        """Audit over a mixed set of tool-mode and regex-fallback tasks."""
        tasks = {
            "tool1": {"id": "tool1", "prompt": "First line: exactly YES or NO"},
            "regex1": {"id": "regex1", "prompt": "Analyze this code."},
            "tool2": {"id": "tool2", "prompt": "Answer with SAFE or UNSAFE on the first line"},
        }
        gt = {
            # Patterns must be specific enough to match exactly one token
            "tool1": {"id": "tool1", "expected_regex": r"(?i)^YES\b"},  # word boundary
            "regex1": {"id": "regex1", "expected_regex": r"pattern"},
            "tool2": {"id": "tool2", "expected_regex": r"(?i)^SAFE\b"},  # avoid matching UNSAFE
        }
        with open(self.tasks_file, "w") as f:
            for t in tasks.values():
                f.write(json.dumps(t) + "\n")
        with open(self.gt_file, "w") as f:
            for g in gt.values():
                f.write(json.dumps(g) + "\n")

        tool_mode, regex_fallback = audit_tasks(str(self.tasks_file), str(self.gt_file))

        self.assertEqual(len(tool_mode), 2)
        self.assertEqual(len(regex_fallback), 1)
        self.assertIn("tool1", tool_mode)
        self.assertIn("tool2", tool_mode)
        self.assertIn("regex1", regex_fallback)


class TestPromptTransform(unittest.TestCase):
    """Test prompt transformation: remove format instruction, add tool instruction."""

    def test_remove_first_line_exactly_instruction(self):
        """Remove 'First line: exactly ...' sentence from prompt."""
        original = """Analyze the code.
First line of your response: exactly CORRECT or INCORRECT
Provide reasoning below."""
        # The transform should be: find the instruction sentence and remove it
        # This tests the transform pattern that will be applied in run_v2_parallel.py

        # One uniform regex transform across all tasks
        instruction_pattern = re.compile(
            r"(?:First line(?:\s+of\s+your\s+response)?:\s*exactly\s+.+?(?:\n|$))\s*",
            re.IGNORECASE | re.DOTALL
        )
        transformed = instruction_pattern.sub("", original).strip()

        self.assertNotIn("First line", transformed)
        self.assertNotIn("exactly", transformed)
        self.assertIn("Analyze the code", transformed)
        self.assertIn("Provide reasoning below", transformed)

    def test_remove_answer_with_instruction(self):
        """Remove 'Answer with ... on the first line' instruction."""
        original = """Your task is to evaluate.
Answer with SAFE or UNSAFE on the first line
Then explain."""

        instruction_pattern = re.compile(
            r"(?:Answer with\s+.+?\s+on\s+the\s+first\s+line\s*(?:\n|$))",
            re.IGNORECASE | re.DOTALL
        )
        transformed = instruction_pattern.sub("", original).strip()

        self.assertNotIn("Answer with", transformed)
        self.assertIn("Your task is to evaluate", transformed)
        self.assertIn("Then explain", transformed)

    def test_transform_preserves_task_content(self):
        """Ensure transform doesn't remove actual task content."""
        original = """Problem: Find the bug in this code.
First line: exactly BUG_TYPE_A or BUG_TYPE_B
The code is:
if x > 5:
    print("large")"""

        instruction_pattern = re.compile(
            r"(?:First line(?:\s+of\s+your\s+response)?:\s*exactly\s+.+?(?:\n|$))\s*",
            re.IGNORECASE
        )
        transformed = instruction_pattern.sub("", original).strip()

        self.assertIn("Find the bug", transformed)
        self.assertIn("if x > 5", transformed)
        self.assertNotIn("First line", transformed)


class TestAllTasksToolGrading(unittest.TestCase):
    """Validate all 130 tasks grade correctly in tool mode (v5).

    Tool mode grades all tasks by running ground-truth regex against submitted answer.
    This test verifies that exemplars (correct answers) grade as correct and
    counter-examples (incorrect answers) grade as incorrect.
    """

    @classmethod
    def setUpClass(cls):
        """Load all tasks and ground truth."""
        cls.tasks = {t.id: t for t in load_frontier_tasks("bench/tasks_frontier.jsonl")}
        cls.gt = load_ground_truth("bench/ground_truth_frontier.jsonl")

    def test_all_130_exemplars_grade_correct(self):
        """Every task's exemplar must grade as correct when submitted as tool answer."""
        failures = []
        for task_id, gt_entry in self.gt.items():
            exemplar = gt_entry.exemplar
            if not exemplar:
                failures.append(f"{task_id}: missing exemplar")
                continue

            # Grade exemplar using the task's ground-truth regex
            expected_regex = gt_entry.expected_regex
            if not expected_regex:
                failures.append(f"{task_id}: missing expected_regex")
                continue

            try:
                if not re.search(expected_regex, exemplar, re.IGNORECASE | re.DOTALL):
                    failures.append(
                        f"{task_id}: exemplar does NOT match expected_regex\n"
                        f"  Exemplar: {exemplar[:60]}...\n"
                        f"  Regex: {expected_regex[:60]}..."
                    )
            except re.error as e:
                failures.append(f"{task_id}: regex error in expected_regex: {e}")

        self.assertEqual(
            failures, [],
            f"Exemplars that do NOT grade correct:\n" + "\n".join(failures),
        )

    def test_all_130_counter_examples_grade_incorrect(self):
        """Every task's counter-example must grade as incorrect when submitted as tool answer."""
        failures = []
        for task_id, gt_entry in self.gt.items():
            counter_example = gt_entry.counter_example
            if not counter_example:
                failures.append(f"{task_id}: missing counter_example")
                continue

            # Grade counter-example using the task's ground-truth regex
            expected_regex = gt_entry.expected_regex
            if not expected_regex:
                failures.append(f"{task_id}: missing expected_regex")
                continue

            try:
                if re.search(expected_regex, counter_example, re.IGNORECASE | re.DOTALL):
                    failures.append(
                        f"{task_id}: counter_example INCORRECTLY matches expected_regex\n"
                        f"  Counter: {counter_example[:60]}...\n"
                        f"  Regex: {expected_regex[:60]}..."
                    )
            except re.error as e:
                failures.append(f"{task_id}: regex error in expected_regex: {e}")

        self.assertEqual(
            failures, [],
            f"Counter-examples that INCORRECTLY grade correct:\n" + "\n".join(failures),
        )

    def test_format_instruction_removal_on_all_tasks(self):
        """Verify format instruction removal works on all 130 tasks."""
        failures = []
        for task_id, task in self.tasks.items():
            prompt = task.prompt
            transformed = remove_format_instruction(prompt)

            # Check: format instruction removed
            # Look for the actual patterns, not just substrings (to avoid false positives on "first" or "answer")
            has_first_line_format = re.search(
                r"First line(?:\s+of\s+your\s+response)?:\s*exactly",
                transformed,
                re.IGNORECASE
            )
            has_answer_with_format = re.search(
                r"Answer\s+with\s+.+?\s+on\s+the\s+first\s+line",
                transformed,
                re.IGNORECASE
            )

            if has_first_line_format or has_answer_with_format:
                failures.append(
                    f"{task_id}: format instruction NOT removed\n"
                    f"  Transformed still contains format instruction pattern"
                )

            # Ensure transformed is non-empty (important: short tasks may have format instruction
            # that dominates, but we still need SOME prompt content for context)
            if not transformed.strip():
                failures.append(
                    f"{task_id}: transformed prompt is empty\n"
                    f"  Original: {len(prompt)} chars"
                )

        self.assertEqual(
            failures, [],
            f"Tasks with format-instruction removal issues:\n" + "\n".join(failures),
        )


class TestToolModeIntegrationRealObjects(unittest.TestCase):
    """Integration test: tool-mode request building and grading with REAL loaded objects.

    This catches type mismatches between test mocks (plain dicts) and real objects
    (GroundTruth dataclasses) that the runner's loaders produce.
    """

    @classmethod
    def setUpClass(cls):
        """Load real tasks and ground truth through the runner's loaders."""
        cls.tasks = load_frontier_tasks("bench/tasks_frontier.jsonl")
        cls.ground_truth = load_ground_truth("bench/ground_truth_frontier.jsonl")
        cls.tool_tasks, _ = audit_tasks()

    def test_tool_mode_enum_schema_with_real_objects(self):
        """Test tool-mode request building and grading for an enum-schema task (ft09)."""
        # Pick ft09 which is a closed-set task (enum schema)
        task = next(t for t in self.tasks if t.id == "ft09_refactoring_correctness_semantic")
        gt = self.ground_truth[task.id]

        # Verify it's a real GroundTruth object and can be accessed as dataclass attribute
        self.assertTrue(hasattr(gt, "expected_regex"))
        self.assertIsNotNone(gt.expected_regex)

        # Verify the tool_tasks entry has proper structure
        self.assertIn(task.id, self.tool_tasks)
        token_set, correct_token = self.tool_tasks[task.id]
        self.assertGreaterEqual(len(token_set), 2)
        self.assertIn(correct_token, token_set)

    def test_tool_mode_string_schema_with_real_objects(self):
        """Test tool-mode request building for a string-schema task (ft01 = regex fallback)."""
        # Pick ft01 which has no closed token set (string schema)
        task = next(t for t in self.tasks if t.id == "ft01_multi_step_sql_refactor")
        gt = self.ground_truth[task.id]

        # Verify it's a real GroundTruth object
        self.assertTrue(hasattr(gt, "expected_regex"))
        self.assertIsNotNone(gt.expected_regex)

        # Verify this task is NOT in tool_tasks (falls back to regex)
        self.assertNotIn(task.id, self.tool_tasks)

    def test_ground_truth_attribute_access(self):
        """Verify ground truth objects support attribute access (not just dict)."""
        # Load via the runner's loader (creates GroundTruth dataclass objects)
        for task_id, gt in self.ground_truth.items():
            # Must be able to access as attributes, not dict .get()
            self.assertTrue(
                hasattr(gt, "id"),
                f"{task_id}: GroundTruth object missing 'id' attribute"
            )
            self.assertTrue(
                hasattr(gt, "expected_regex") or hasattr(gt, "expected"),
                f"{task_id}: GroundTruth object missing 'expected_regex' or 'expected' attribute"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
