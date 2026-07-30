#!/usr/bin/env python3
"""Test frontier slice: validate all tasks' ground-truth patterns.

This test suite verifies:
1. Each task's ground_truth pattern is well-formed (valid regex or exact string)
2. The pattern MATCHES the exemplar answer (proves pattern is not over-strict)
3. The pattern DOES NOT MATCH the counter_example (proves pattern rejects wrong answers)

This is the credibility mechanism: patterns are committed before any v2 results exist.
"""

import unittest
import json
import re
import sys
from pathlib import Path

# Load all 60 tasks and ground truth
def load_tasks(path: str = "bench/tasks_frontier.jsonl") -> dict:
    """Load tasks into dict keyed by id."""
    tasks = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                tasks[obj["id"]] = obj
    return tasks

def load_ground_truth(path: str = "bench/ground_truth_frontier.jsonl") -> dict:
    """Load ground truth into dict keyed by id."""
    gt = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                gt[obj["id"]] = obj
    return gt

class TestFrontierSliceN60(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Load tasks and ground truth once for the entire test class."""
        cls.tasks = load_tasks()
        cls.gt = load_ground_truth()
        cls.n_tasks = len(cls.tasks)

    def test_all_tasks_loaded(self):
        """Verify we loaded all tasks with matching ground truth."""
        assert self.n_tasks > 0, f"No tasks loaded"
        assert len(self.gt) == self.n_tasks, f"Expected {self.n_tasks} ground truths, got {len(self.gt)}"

        # Verify all task IDs match
        assert set(self.tasks.keys()) == set(self.gt.keys()), "Task IDs don't match ground truth IDs"

        print(f"✓ Loaded all {self.n_tasks} tasks and ground truth")

    def test_task_ids_sequential(self):
        """Verify task IDs follow the pattern ft01-ftNN (numeric sort)."""
        # Sort task IDs numerically by extracting the number
        def extract_ft_number(task_id):
            """Extract numeric part from ftNN_* format."""
            parts = task_id.split('_', 1)
            if parts[0].startswith('ft'):
                try:
                    return int(parts[0][2:])
                except ValueError:
                    return float('inf')
            return float('inf')

        task_ids = sorted(self.tasks.keys(), key=extract_ft_number)

        for i, task_id in enumerate(task_ids, 1):
            expected_id = f"ft{i:02d}_"
            assert task_id.startswith(expected_id), f"Position {i}: Expected {expected_id}*, got {task_id}"

        print(f"✓ All {self.n_tasks} task IDs are sequential (ft01 through ft{self.n_tasks:02d})")

    def test_patterns_are_valid(self):
        """Verify each task's ground truth pattern is well-formed."""
        for task_id, gt_obj in self.gt.items():
            task = self.tasks[task_id]
            match_type = task["match"]

            if match_type == "regex":
                pattern = gt_obj.get("expected_regex")
                assert pattern, f"{task_id}: regex task missing expected_regex"

                # Verify regex is valid
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise AssertionError(f"{task_id}: invalid regex pattern: {e}")

            elif match_type == "exact":
                expected = gt_obj.get("expected")
                assert expected, f"{task_id}: exact task missing expected"

            else:
                raise AssertionError(f"{task_id}: unknown match type: {match_type}")

        print(f"✓ All {self.n_tasks} patterns are well-formed")

    def test_exemplar_matches(self):
        """Verify exemplar answer MATCHES its pattern."""
        failures = []

        for task_id, gt_obj in self.gt.items():
            task = self.tasks[task_id]
            match_type = task["match"]
            exemplar = gt_obj.get("exemplar")

            assert exemplar, f"{task_id}: missing exemplar"

            if match_type == "regex":
                pattern = gt_obj.get("expected_regex")
                if not re.search(pattern, exemplar, re.IGNORECASE | re.DOTALL):
                    failures.append(f"{task_id}: exemplar DOES NOT match regex pattern")

            elif match_type == "exact":
                expected = gt_obj.get("expected")
                if exemplar.strip().lower() != expected.lower():
                    failures.append(f"{task_id}: exemplar DOES NOT match exact expectation")

        if failures:
            print("\n".join(failures))
            raise AssertionError(f"Found {len(failures)} exemplar matching failures")

        print(f"✓ All {self.n_tasks} exemplars match their patterns")

    def test_counter_example_rejects(self):
        """Verify counter_example answer DOES NOT match the pattern."""
        failures = []

        for task_id, gt_obj in self.gt.items():
            task = self.tasks[task_id]
            match_type = task["match"]
            counter = gt_obj.get("counter_example")

            assert counter, f"{task_id}: missing counter_example"

            if match_type == "regex":
                pattern = gt_obj.get("expected_regex")
                if re.search(pattern, counter, re.IGNORECASE | re.DOTALL):
                    failures.append(f"{task_id}: counter_example INCORRECTLY matches regex pattern")

            elif match_type == "exact":
                expected = gt_obj.get("expected")
                if counter.strip().lower() == expected.lower():
                    failures.append(f"{task_id}: counter_example INCORRECTLY matches exact expectation")

        if failures:
            print("\n".join(failures))
            raise AssertionError(f"Found {len(failures)} counter_example rejection failures")

        print(f"✓ All {self.n_tasks} counter_examples are correctly rejected by their patterns")

    def test_no_duplicate_categories(self):
        """Verify category distribution (optional: just report)."""
        categories = {}
        for task in self.tasks.values():
            cat = task["category"]
            categories[cat] = categories.get(cat, 0) + 1

        print(f"\n  Category distribution (N={self.n_tasks}):")
        for cat, count in sorted(categories.items()):
            print(f"    {cat}: {count} tasks")

    def test_discrimination_rationale_present(self):
        """Verify each task has a discrimination_rationale."""
        for task_id, task in self.tasks.items():
            assert "discrimination_rationale" in task, f"{task_id}: missing discrimination_rationale"
            assert len(task["discrimination_rationale"]) > 20, f"{task_id}: rationale too short"

        print(f"✓ All {self.n_tasks} tasks have discrimination rationales")

if __name__ == "__main__":
    unittest.main()
