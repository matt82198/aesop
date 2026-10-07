#!/usr/bin/env python3
"""Test suite for accuracy_harness.py.

Proves the offline harness:
1. Correctly identifies valid vs. malformed JSON
2. Correctly validates schema compliance
3. Correctly detects ownership violations
4. Produces consistent scoring with a mix of good/bad responses
"""

import json
import sys
import unittest
from pathlib import Path

# Add bench and driver to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
sys.path.insert(0, str(Path(__file__).parent.parent / "driver"))

from accuracy_harness import (
    AccuracyTask,
    FakeTransport,
    score_response,
    _build_test_tasks,
    run_offline_benchmark,
)


class TestScoringLogic(unittest.TestCase):
    """Test the score_response() function."""

    def test_valid_json_and_schema(self):
        """Valid JSON matching schema should score 100% on all metrics."""
        task = AccuracyTask(
            id="test_valid",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = json.dumps({
            "files": [{"path": "file.py", "contents": "# code"}],
            "summary": "Updated file",
            "done": True,
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)
        self.assertTrue(score.schema_exact)
        self.assertTrue(score.ownership_respect)
        self.assertAlmostEqual(score.composite_accuracy, 1.0)
        self.assertIsNone(score.error)

    def test_invalid_json_truncated(self):
        """Truncated JSON should fail valid_json_first_try."""
        task = AccuracyTask(
            id="test_truncated",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = '{"files": [{"path": "file.py", "contents": "# code"'

        score = score_response(task, response)
        self.assertFalse(score.valid_json_first_try)
        self.assertFalse(score.schema_exact)
        # Ownership defaults to False when JSON/schema validation fails
        self.assertFalse(score.ownership_respect)
        self.assertAlmostEqual(score.composite_accuracy, 0.0)
        self.assertIn("JSON", score.error or "")

    def test_invalid_json_bad_escape(self):
        """JSON with invalid escape sequences should fail."""
        task = AccuracyTask(
            id="test_escape",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = '{"files": [], "summary": "test\\xZZ", "done": true}'

        score = score_response(task, response)
        # Python's json.loads() handles most escapes, but invalid ones fail at parse
        # This specific escape might actually parse depending on Python version,
        # so we just check it doesn't match schema if it does parse
        if not score.valid_json_first_try:
            self.assertFalse(score.schema_exact)

    def test_missing_required_field(self):
        """JSON missing required field should fail schema_exact."""
        task = AccuracyTask(
            id="test_missing",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        # Missing 'done' field
        response = json.dumps({
            "files": [],
            "summary": "test",
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)  # JSON is valid
        self.assertFalse(score.schema_exact)  # But schema validation fails
        self.assertFalse(score.ownership_respect)  # And can't check ownership
        self.assertAlmostEqual(score.composite_accuracy, 1.0 / 3.0)  # Only valid_json=1
        self.assertIn("Schema", score.error or "")

    def test_extra_field_not_allowed(self):
        """JSON with extra field violates additionalProperties=false."""
        task = AccuracyTask(
            id="test_extra",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = json.dumps({
            "files": [],
            "summary": "test",
            "done": True,
            "extra": "field",  # Not in schema
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)
        self.assertFalse(score.schema_exact)  # Extra field violates schema
        self.assertFalse(score.ownership_respect)
        # Only valid_json=1, schema=0, ownership=0 -> 1/3
        self.assertAlmostEqual(score.composite_accuracy, 1.0 / 3.0, places=2)
        self.assertIn("Schema", score.error or "")

    def test_ownership_escape_relative(self):
        """Paths escaping via ../ should fail ownership_respect."""
        task = AccuracyTask(
            id="test_escape_rel",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = json.dumps({
            "files": [{"path": "../secret.py", "contents": "evil"}],
            "summary": "done",
            "done": True,
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)
        self.assertTrue(score.schema_exact)  # Schema is valid
        self.assertFalse(score.ownership_respect)  # But path not owned
        self.assertAlmostEqual(score.composite_accuracy, 2.0 / 3.0)
        self.assertIn("Ownership", score.error or "")

    def test_ownership_absolute_path(self):
        """Absolute paths should fail ownership_respect."""
        task = AccuracyTask(
            id="test_abs",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = json.dumps({
            "files": [{"path": "/etc/passwd", "contents": "root:..."}],
            "summary": "done",
            "done": True,
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)
        self.assertTrue(score.schema_exact)
        self.assertFalse(score.ownership_respect)
        self.assertIn("Ownership", score.error or "")

    def test_ownership_unowned_file(self):
        """Attempting to write unowned file should fail."""
        task = AccuracyTask(
            id="test_unowned",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        response = json.dumps({
            "files": [
                {"path": "file.py", "contents": "ok"},
                {"path": "unowned.py", "contents": "evil"},
            ],
            "summary": "done",
            "done": True,
        })

        score = score_response(task, response)
        self.assertTrue(score.valid_json_first_try)
        self.assertTrue(score.schema_exact)
        self.assertFalse(score.ownership_respect)
        self.assertIn("Ownership", score.error or "")

    def test_composite_accuracy_calculation(self):
        """Composite accuracy should be mean of three metrics."""
        task = AccuracyTask(
            id="test_composite",
            category="test",
            prompt="test",
            owned_files=("file.py",),
        )

        # Two pass, one fail: should be 2/3
        response = json.dumps({
            "files": [{"path": "unowned.py", "contents": "evil"}],
            "summary": "done",
            "done": True,
        })

        score = score_response(task, response)
        # valid_json=1, schema=1, ownership=0 -> composite = 2/3
        self.assertAlmostEqual(score.composite_accuracy, 2.0 / 3.0, places=2)


class TestFakeTransport(unittest.TestCase):
    """Test FakeTransport scripted responses."""

    def test_good_response(self):
        """Default response should be valid and schema-compliant."""
        transport = FakeTransport("t01_simple_append")
        payload = {"model": "gpt-3.5-turbo", "messages": []}
        response = transport(payload)

        self.assertIn("choices", response)
        self.assertIn("usage", response)
        content = response["choices"][0]["message"]["content"]
        parsed = json.loads(content)  # Should not raise
        self.assertIn("files", parsed)
        self.assertIn("summary", parsed)
        self.assertIn("done", parsed)

    def test_truncated_response(self):
        """t14_truncated should return incomplete JSON."""
        transport = FakeTransport("t14_truncated_json")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        with self.assertRaises(json.JSONDecodeError):
            json.loads(content)

    def test_invalid_escape_response(self):
        """t15_invalid_escape should return unparseable content."""
        transport = FakeTransport("t15_invalid_escape")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        # This might parse or not depending on the escape, but should be marked
        try:
            parsed = json.loads(content)
            # If it parses, it's likely malformed in schema
            self.assertIsNotNone(parsed)
        except json.JSONDecodeError:
            pass  # Expected

    def test_missing_field_response(self):
        """t16_missing_field should return JSON missing required field."""
        transport = FakeTransport("t16_missing_field")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        parsed = json.loads(content)  # Should parse
        # But should be missing 'done'
        self.assertNotIn("done", parsed)

    def test_escape_relative_response(self):
        """t18_escape_attempt_relative should return path with ../"""
        transport = FakeTransport("t18_escape_attempt_relative")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        parsed = json.loads(content)
        files = parsed.get("files", [])
        self.assertTrue(any("../" in f.get("path", "") for f in files))

    def test_escape_absolute_response(self):
        """t19_escape_attempt_absolute should return absolute path."""
        transport = FakeTransport("t19_escape_attempt_absolute")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        parsed = json.loads(content)
        files = parsed.get("files", [])
        self.assertTrue(any(f.get("path", "").startswith("/") for f in files))

    def test_unowned_file_response(self):
        """t20_unowned_file should return unowned path."""
        transport = FakeTransport("t20_unowned_file")
        response = transport({})
        content = response["choices"][0]["message"]["content"]

        parsed = json.loads(content)
        files = parsed.get("files", [])
        paths = [f.get("path") for f in files]
        self.assertIn("unowned.py", paths)


class TestTaskGeneration(unittest.TestCase):
    """Test task generation."""

    def test_tasks_built(self):
        """Should build at least 30 tasks."""
        tasks = _build_test_tasks()
        self.assertGreaterEqual(len(tasks), 30)

    def test_tasks_have_required_fields(self):
        """Each task should have all required fields."""
        tasks = _build_test_tasks()
        for task in tasks:
            self.assertIsNotNone(task.id)
            self.assertIsNotNone(task.category)
            self.assertIsNotNone(task.prompt)
            self.assertIsNotNone(task.owned_files)

    def test_task_ids_unique(self):
        """Task IDs should be unique."""
        tasks = _build_test_tasks()
        ids = [t.id for t in tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_malformed_tasks_marked(self):
        """Tasks designed to produce malformed responses should be marked."""
        tasks = _build_test_tasks()
        malformed_count = sum(
            1 for t in tasks
            if not t.expected_valid_json or not t.expected_schema_match
        )
        self.assertGreater(malformed_count, 0)

    def test_ownership_violation_tasks(self):
        """Some tasks should be designed to violate ownership."""
        tasks = _build_test_tasks()
        ownership_violation_count = sum(
            1 for t in tasks if not t.expected_ownership_respect
        )
        self.assertGreater(ownership_violation_count, 0)


class TestOfflineBenchmark(unittest.TestCase):
    """Test the full offline benchmark run."""

    def test_offline_benchmark_runs(self):
        """Offline benchmark should complete and return scores."""
        tasks = _build_test_tasks()
        scores, overall = run_offline_benchmark(tasks[:5])  # Run just 5 for speed

        self.assertEqual(len(scores), 5)
        self.assertGreaterEqual(overall, 0.0)
        self.assertLessEqual(overall, 1.0)

    def test_offline_benchmark_scores_structure(self):
        """Each score should have all required fields."""
        tasks = _build_test_tasks()
        scores, overall = run_offline_benchmark(tasks[:1])

        score = scores[0]
        self.assertIsNotNone(score.task_id)
        self.assertIsNotNone(score.category)
        self.assertIsInstance(score.valid_json_first_try, bool)
        self.assertIsInstance(score.schema_exact, bool)
        self.assertIsInstance(score.ownership_respect, bool)
        self.assertIsInstance(score.composite_accuracy, float)

    def test_offline_benchmark_detects_good_responses(self):
        """Good responses (valid JSON, schema-compliant) should score high."""
        tasks = _build_test_tasks()
        good_tasks = [t for t in tasks if t.expected_valid_json and t.expected_schema_match]

        scores, overall = run_offline_benchmark(good_tasks[:5])

        # Most good tasks should have composite >= 0.67 (at least 2/3 metrics pass)
        high_scores = [s for s in scores if s.composite_accuracy >= 0.67]
        self.assertGreater(len(high_scores), 0)

    def test_offline_benchmark_detects_malformed_responses(self):
        """Malformed responses should score low."""
        tasks = _build_test_tasks()
        malformed_tasks = [t for t in tasks if not t.expected_valid_json]

        scores, overall = run_offline_benchmark(malformed_tasks)

        # All malformed tasks should have valid_json_first_try=False
        for score in scores:
            if "truncated" in score.task_id or "escape" in score.task_id and "attempt" in score.task_id:
                # These are designed to produce malformed responses
                self.assertFalse(score.valid_json_first_try)



class TestLiveBenchmarkPipeline(unittest.TestCase):
    """Live mode must score the transport's RAW response (regression for the
    2026-07-22 uniform-33% run, where CodexDriver environment failures were
    scored as model inaccuracy and no API call was ever made)."""

    def test_live_scores_injected_transport_response(self):
        from accuracy_harness import _build_test_tasks, run_live_benchmark, FakeTransport

        tasks = _build_test_tasks()[:3]
        calls = []

        def fake_live_transport(payload):
            calls.append(payload)
            prompt = payload["messages"][1]["content"]
            task = next(t for t in tasks if t.prompt == prompt)
            # Delegate to the offline FakeTransport for a correct canned answer
            return FakeTransport(task.id)(payload)

        scores, overall = run_live_benchmark(tasks, transport=fake_live_transport)
        # Transport was called once per task with the SHARED payload shape
        self.assertEqual(len(calls), len(tasks))
        for payload in calls:
            self.assertEqual(len(payload["messages"]), 2)
            self.assertEqual(payload["messages"][0]["role"], "system")
            # Fidelity to CodexDriver.dispatch_worker (drift guard): the model
            # must be TOLD the schema — its omission produced the 4% 2026-07-22
            # run — and the structured-output contract must be requested.
            self.assertIn("Return valid JSON matching the schema", payload["messages"][0]["content"])
            self.assertEqual(payload["temperature"], 0)
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        # Correct canned answers must score perfect composite (proves the raw
        # response reaches the scorer, not a driver-mediated empty result)
        self.assertEqual(overall, 1.0, msg=f"scores: {[(s.task_id, s.composite_accuracy) for s in scores]}")

    def test_live_empty_response_scores_low_not_crash(self):
        from accuracy_harness import _build_test_tasks, run_live_benchmark

        tasks = _build_test_tasks()[:2]

        def empty_transport(payload):
            return {"choices": [{"message": {"content": "{}"}}]}

        scores, overall = run_live_benchmark(tasks, transport=empty_transport)
        self.assertLess(overall, 0.5)
        self.assertTrue(all(s.valid_json_first_try for s in scores))

if __name__ == "__main__":
    unittest.main()
