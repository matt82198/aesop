"""
Tests for the coding grader.

Verifies that:
1. A known-correct solution passes all test cases
2. A known-buggy solution fails some test cases
"""

import unittest
import tempfile
import os
import sys
from pathlib import Path

# Add bench to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bench.coding_grader import grade_solution


class TestCodingGrader(unittest.TestCase):
    """Test the coding grader with correct and buggy solutions."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_correct_fizzbuzz_solution(self):
        """Test that a correct FizzBuzz solution passes all tests."""
        solution_code = '''
def fizzbuzz(n):
    """Generate FizzBuzz sequence from 1 to n."""
    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return ",".join(result)
'''

        solution_file = os.path.join(self.temp_dir, "fizzbuzz_correct.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fizzbuzz", solution_file)

        self.assertTrue(result["success"], f"Expected success, got: {result}")
        self.assertEqual(result["passed"], result["total"],
                         f"Expected all tests to pass, got {result['passed']}/{result['total']}")

    def test_buggy_fizzbuzz_solution(self):
        """Test that a buggy FizzBuzz solution fails some tests."""
        # Bug: forgets to check % 15 first, so FizzBuzz never appears
        solution_code = '''
def fizzbuzz(n):
    """Buggy FizzBuzz that never produces FizzBuzz."""
    result = []
    for i in range(1, n + 1):
        if i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return ",".join(result)
'''

        solution_file = os.path.join(self.temp_dir, "fizzbuzz_buggy.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fizzbuzz", solution_file)

        self.assertFalse(result["success"], f"Expected failure, got: {result}")
        self.assertLess(result["passed"], result["total"],
                        f"Expected some tests to fail, got {result['passed']}/{result['total']}")

    def test_correct_is_palindrome(self):
        """Test that a correct is_palindrome solution passes."""
        solution_code = '''
def is_palindrome(s):
    """Check if string is palindrome, ignoring spaces and case."""
    cleaned = s.lower().replace(" ", "")
    return cleaned == cleaned[::-1]
'''

        solution_file = os.path.join(self.temp_dir, "palindrome_correct.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("is_palindrome", solution_file)

        self.assertTrue(result["success"], f"Expected success, got: {result}")
        self.assertEqual(result["passed"], result["total"])

    def test_buggy_is_palindrome(self):
        """Test that a buggy is_palindrome solution fails."""
        # Bug: forgets to ignore spaces and case
        solution_code = '''
def is_palindrome(s):
    """Buggy: doesn't ignore spaces or case."""
    return s == s[::-1]
'''

        solution_file = os.path.join(self.temp_dir, "palindrome_buggy.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("is_palindrome", solution_file)

        self.assertFalse(result["success"], f"Expected failure")
        self.assertLess(result["passed"], result["total"])

    def test_correct_reverse_string(self):
        """Test that a correct reverse_string solution passes."""
        solution_code = '''
def reverse_string(s):
    """Reverse string without slicing."""
    result = ""
    for char in s:
        result = char + result
    return result
'''

        solution_file = os.path.join(self.temp_dir, "reverse_correct.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("reverse_string", solution_file)

        self.assertTrue(result["success"], f"Expected success, got: {result}")

    def test_buggy_reverse_string(self):
        """Test that a buggy reverse_string solution fails."""
        # Bug: uses slicing (violates spec)
        solution_code = '''
def reverse_string(s):
    """Wrong: uses slicing."""
    return s[::-1]
'''

        solution_file = os.path.join(self.temp_dir, "reverse_buggy.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        # Note: This will actually pass since it's technically correct output
        # Let's use a different bug
        solution_code = '''
def reverse_string(s):
    """Buggy: returns original string."""
    return s
'''

        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("reverse_string", solution_file)

        self.assertFalse(result["success"], f"Expected failure")
        self.assertLess(result["passed"], result["total"])

    def test_correct_fibonacci(self):
        """Test that a correct fibonacci solution passes."""
        solution_code = '''
def fibonacci(n):
    """Get nth Fibonacci number."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''

        solution_file = os.path.join(self.temp_dir, "fib_correct.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fibonacci_nth", solution_file)

        self.assertTrue(result["success"], f"Expected success, got: {result}")

    def test_buggy_fibonacci(self):
        """Test that a buggy fibonacci solution fails."""
        # Bug: off-by-one error
        solution_code = '''
def fibonacci(n):
    """Buggy: off-by-one."""
    if n <= 0:
        return 0
    if n == 1:
        return 0  # Bug: should be 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''

        solution_file = os.path.join(self.temp_dir, "fib_buggy.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fibonacci_nth", solution_file)

        self.assertFalse(result["success"], f"Expected failure")
        self.assertLess(result["passed"], result["total"])

    def test_correct_binary_search(self):
        """Test that a correct binary_search solution passes."""
        solution_code = '''
def binary_search(lst, target):
    """Binary search on sorted list."""
    left, right = 0, len(lst) - 1
    while left <= right:
        mid = (left + right) // 2
        if lst[mid] == target:
            return mid
        elif lst[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
'''

        solution_file = os.path.join(self.temp_dir, "bsearch_correct.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("binary_search", solution_file)

        self.assertTrue(result["success"], f"Expected success, got: {result}")

    def test_buggy_binary_search(self):
        """Test that a buggy binary_search solution fails."""
        # Bug: doesn't handle not found case correctly
        solution_code = '''
def binary_search(lst, target):
    """Buggy: doesn't return -1 on not found."""
    left, right = 0, len(lst) - 1
    while left <= right:
        mid = (left + right) // 2
        if lst[mid] == target:
            return mid
        elif lst[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return None  # Bug: should be -1
'''

        solution_file = os.path.join(self.temp_dir, "bsearch_buggy.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("binary_search", solution_file)

        self.assertFalse(result["success"], f"Expected failure")

    def test_unknown_task(self):
        """Test that grading unknown task returns error."""
        solution_file = os.path.join(self.temp_dir, "dummy.py")
        with open(solution_file, 'w') as f:
            f.write("def dummy(): pass")

        result = grade_solution("unknown_task", solution_file)

        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_missing_file(self):
        """Test that grading missing file returns error."""
        result = grade_solution("fizzbuzz", "/nonexistent/path/file.py")

        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_syntax_error(self):
        """Test that syntax errors in solution are caught."""
        solution_code = '''
def fizzbuzz(n):
    this is not valid python!
'''

        solution_file = os.path.join(self.temp_dir, "syntax_error.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fizzbuzz", solution_file)

        self.assertFalse(result["success"])
        # Should have error in results
        self.assertTrue(any("error" in r for r in result.get("results", [])))

    def test_result_structure(self):
        """Test that grade result has expected structure."""
        solution_code = '''
def fizzbuzz(n):
    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return ",".join(result)
'''

        solution_file = os.path.join(self.temp_dir, "structure_test.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fizzbuzz", solution_file)

        # Check structure
        self.assertIn("task_id", result)
        self.assertIn("passed", result)
        self.assertIn("total", result)
        self.assertIn("results", result)
        self.assertIn("success", result)

        # Check results structure
        for r in result["results"]:
            self.assertIn("test", r)
            self.assertIn("input", r)
            self.assertIn("expected", r)
            self.assertIn("passed", r)

    def test_malicious_entrypoint_rejected(self):
        """Test that malicious entrypoint names are rejected (SECURITY)."""
        # Create a task with a malicious entrypoint that tries code injection
        solution_code = '''
def fizzbuzz(n):
    return "1,2,Fizz"
'''

        solution_file = os.path.join(self.temp_dir, "malicious.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        # Import and create a tasks.jsonl with malicious entrypoint
        tasks_path = Path(__file__).parent.parent / "bench" / "coding_tasks.jsonl"

        # Create a malicious entry (must be done carefully to not pollute repo)
        # Instead, we'll directly test the entrypoint validation by monkeypatching
        from bench.coding_grader import VALID_ENTRYPOINT_PATTERN

        # Test pattern validation
        self.assertTrue(VALID_ENTRYPOINT_PATTERN.match("fizzbuzz"))
        self.assertTrue(VALID_ENTRYPOINT_PATTERN.match("_private"))
        self.assertTrue(VALID_ENTRYPOINT_PATTERN.match("func123"))

        # These should NOT match
        self.assertFalse(VALID_ENTRYPOINT_PATTERN.match("123invalid"))
        self.assertFalse(VALID_ENTRYPOINT_PATTERN.match("func-name"))
        self.assertFalse(VALID_ENTRYPOINT_PATTERN.match("func name"))
        self.assertFalse(VALID_ENTRYPOINT_PATTERN.match("func();"))
        self.assertFalse(VALID_ENTRYPOINT_PATTERN.match("func.method"))

    def test_prose_wrapped_solution_extracted(self):
        """Test that a correct solution wrapped in prose/markdown is extracted and grades PASS."""
        # Model output might look like this: prose explanation + code fence + function
        solution_code = '''
Here is my solution for fizzbuzz:

```python
def fizzbuzz(n):
    """Generate FizzBuzz sequence from 1 to n."""
    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return ",".join(result)
```

This solution correctly handles all three conditions.
'''

        solution_file = os.path.join(self.temp_dir, "prose_wrapped.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("fizzbuzz", solution_file)

        # Should pass despite prose wrapper
        self.assertTrue(result["success"],
                       f"Expected prose-wrapped solution to pass, got: {result}")
        self.assertEqual(result["passed"], result["total"],
                        f"Expected all tests to pass for prose-wrapped solution")

    def test_remove_duplicates_works(self):
        """Test that remove_duplicates task works correctly (BUG FIX)."""
        # The bug was that lists were treated as multiple arguments instead of single arg
        solution_code = '''
def remove_duplicates(lst):
    """Remove duplicates while preserving order."""
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
'''

        solution_file = os.path.join(self.temp_dir, "remove_dups.py")
        with open(solution_file, 'w') as f:
            f.write(solution_code)

        result = grade_solution("remove_duplicates", solution_file)

        self.assertTrue(result["success"],
                       f"Expected remove_duplicates to pass, got: {result}")
        self.assertEqual(result["passed"], result["total"],
                        f"Expected all remove_duplicates tests to pass")


if __name__ == "__main__":
    unittest.main()
