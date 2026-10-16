"""
External coding grader with hidden test cases.

This grader evaluates candidate solutions against objective test cases
that are NOT shown in the task prompts, ensuring honest evaluation.

Deterministic on Linux, macOS, and Windows:
  * Entrypoint names are embedded (DEFAULT_ENTRYPOINTS) so the grader is fully
    self-contained and does not depend on any git-ignored task file existing.
  * Candidate solutions run under the SAME interpreter (sys.executable) that
    invoked the grader, so no hardcoded ``python``/``python3`` assumption.
  * Each candidate runs in its own process group with a hard timeout; on
    timeout the whole group is killed so a hanging candidate cannot wedge CI.
  * Subprocess I/O is decoded as UTF-8 (errors replaced) and all emitted text
    is ASCII, so a non-ASCII candidate cannot crash the grader on cp1252.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple


# Hidden test cases for each task (not shown to the model)
HIDDEN_TEST_CASES = {
    "fizzbuzz": [
        (1, "1"),
        (3, "1,2,Fizz"),
        (5, "1,2,Fizz,4,Buzz"),
        (15, "1,2,Fizz,4,Buzz,Fizz,7,8,Fizz,Buzz,11,Fizz,13,14,FizzBuzz"),
        (30, "1,2,Fizz,4,Buzz,Fizz,7,8,Fizz,Buzz,11,Fizz,13,14,FizzBuzz,16,17,Fizz,19,Buzz,Fizz,22,23,Fizz,Buzz,26,Fizz,28,29,FizzBuzz"),
    ],
    "is_palindrome": [
        ("a", True),
        ("racecar", True),
        ("A man a plan a canal Panama", True),
        ("hello", False),
        ("was it a car or a cat i saw", True),
        ("Madam", True),
        ("abcdef", False),
        ("12321", True),
        ("1234", False),
    ],
    "find_prime": [
        (2, True),
        (3, True),
        (4, False),
        (5, True),
        (17, True),
        (20, False),
        (97, True),
        (100, False),
        (1, False),
        (0, False),
    ],
    "reverse_string": [
        ("hello", "olleh"),
        ("a", "a"),
        ("ab", "ba"),
        ("abc", "cba"),
        ("12345", "54321"),
        ("", ""),
    ],
    "remove_duplicates": [
        ([1, 2, 2, 3], [1, 2, 3]),
        ([1], [1]),
        ([], []),
        ([1, 1, 1], [1]),
        ([5, 4, 3, 2, 1], [5, 4, 3, 2, 1]),
        ([1, 2, 1, 3, 2, 4], [1, 2, 3, 4]),
    ],
    "fibonacci_nth": [
        (0, 0),
        (1, 1),
        (2, 1),
        (3, 2),
        (4, 3),
        (5, 5),
        (6, 8),
        (10, 55),
    ],
    "binary_search": [
        ([1, 3, 5, 7], 5, 2),
        ([1, 3, 5, 7], 1, 0),
        ([1, 3, 5, 7], 7, 3),
        ([1, 3, 5, 7], 4, -1),
        ([1], 1, 0),
        ([1], 2, -1),
        ([], 1, -1),
        ([1, 2, 3, 4, 5], 1, 0),
    ],
    "count_vowels": [
        ("hello", 2),
        ("aeiou", 5),
        ("AEIOU", 5),
        ("bcdfg", 0),
        ("", 0),
        ("Hello World", 3),
        ("Python", 1),
    ],
}

# Embedded entrypoint (function name) for each task. Keeping this in-code makes
# the grader deterministic on any fresh checkout: the hidden test cases already
# live here, so the entrypoint map belongs here too. An optional
# ``coding_tasks.jsonl`` beside this module may override/extend these, but its
# absence is NOT an error (that file is git-ignored via ``*.jsonl``).
DEFAULT_ENTRYPOINTS = {
    "fizzbuzz": "fizzbuzz",
    "is_palindrome": "is_palindrome",
    "find_prime": "is_prime",
    "reverse_string": "reverse_string",
    "remove_duplicates": "remove_duplicates",
    "fibonacci_nth": "fibonacci",
    "binary_search": "binary_search",
    "count_vowels": "count_vowels",
}

TIMEOUT_SECONDS = 5

# Regex for valid Python identifiers (function/variable names)
VALID_ENTRYPOINT_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _extract_code_from_prose(text: str) -> str:
    """
    Extract Python code from text that may contain markdown fences or prose.

    Handles:
    - Triple-backtick Python fences (```python ... ```)
    - Prose lines outside of fences
    - Preserves the core function definition

    If no fences are found, returns text as-is (assumes it's all code).

    Args:
        text: Raw text potentially containing prose, markdown, or both

    Returns:
        Cleaned Python code ready for execution
    """
    lines = text.split('\n')

    # First pass: check if there are any markdown fences
    has_fences = any(line.rstrip().startswith('```') for line in lines)

    # If no fences found, assume it's all code (common case for test solutions)
    if not has_fences:
        return text

    # Second pass: extract code from between fences
    cleaned = []
    in_code_fence = False

    for line in lines:
        stripped = line.rstrip()

        # Detect markdown fence (``` or ```python, etc)
        if stripped.startswith('```'):
            in_code_fence = not in_code_fence
            # Don't include the fence markers themselves
            continue

        # If we're inside a code fence, include the line
        if in_code_fence:
            cleaned.append(line)

    return '\n'.join(cleaned)


def _resolve_entrypoint(task_id: str) -> Optional[str]:
    """Return the entrypoint function name for ``task_id``.

    Uses the embedded DEFAULT_ENTRYPOINTS first so a missing task file never
    breaks grading. If an optional ``coding_tasks.jsonl`` exists beside this
    module it may override the default, but any read/parse problem silently
    falls back to the embedded value.
    """
    entrypoint = DEFAULT_ENTRYPOINTS.get(task_id)

    tasks_path = Path(__file__).parent / "coding_tasks.jsonl"
    if tasks_path.exists():
        try:
            with open(tasks_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    task = json.loads(line)
                    if task.get("id") == task_id and task.get("entrypoint"):
                        entrypoint = task["entrypoint"]
                        break
        except (OSError, ValueError):
            # Corrupt or unreadable optional file: keep the embedded default.
            pass

    return entrypoint


# Environment variables a candidate may inherit. Everything else is dropped,
# so API keys, tokens and secrets in the grader's environment never reach
# model-generated code. This is an ALLOWLIST: new secret-bearing variables are
# excluded by default without needing a denylist update.
_CANDIDATE_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
)


def _candidate_env(cwd: str) -> Dict[str, str]:
    """Return the minimal environment a candidate process runs with.

    Only ``_CANDIDATE_ENV_ALLOWLIST`` entries are copied from the grader's
    environment. ``cwd`` is the per-candidate scratch directory, exported as
    PWD so tools that read it see the sandbox rather than the repo.
    """
    env = {
        name: os.environ[name]
        for name in _CANDIDATE_ENV_ALLOWLIST
        if name in os.environ
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PWD"] = cwd
    return env


def _run_candidate(cmd, timeout: int, cwd: str) -> Tuple[Optional[int], str, str, bool]:
    """Run ``cmd`` with a hard timeout, killing the whole process group on expiry.

    The child gets a scrubbed environment (``_candidate_env``) and ``cwd`` as its
    working directory. Returns ``(returncode, stdout, stderr, timed_out)``.
    ``returncode`` is None when the candidate timed out. stdin is closed so a
    candidate cannot block on input, and output is decoded as UTF-8 (bad bytes
    replaced) so a non-ASCII candidate never crashes the grader on a cp1252 console.
    """
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_candidate_env(cwd),
        cwd=cwd,
    )
    # Put the child in its own process group so we can kill descendants too.
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return None, stdout, stderr, True


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort hard kill of a subprocess and any children it spawned."""
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def grade_solution(task_id: str, solution_file: str) -> Dict:
    """
    Grade a candidate solution against hidden test cases.

    Args:
        task_id: ID of the task (key in HIDDEN_TEST_CASES)
        solution_file: Path to file containing the candidate solution

    Returns:
        Dict with keys:
            - task_id: The task ID
            - passed: Number of test cases passed
            - total: Total number of test cases
            - results: List of per-case results
            - success: True if all tests passed
            - error: Error message if grading failed
    """

    if task_id not in HIDDEN_TEST_CASES:
        return {
            "task_id": task_id,
            "passed": 0,
            "total": 0,
            "results": [],
            "success": False,
            "error": f"Unknown task: {task_id}",
        }

    # Read the solution file
    try:
        with open(solution_file, "r", encoding="utf-8") as f:
            raw_solution = f.read()
    except Exception as e:
        return {
            "task_id": task_id,
            "passed": 0,
            "total": 0,
            "results": [],
            "success": False,
            "error": f"Failed to read solution file: {e}",
        }

    # FAIRNESS: Extract code from markdown/prose-wrapped solutions.
    # This handles models that emit ```python ... ``` fences or explanatory prose.
    solution_code = _extract_code_from_prose(raw_solution)

    # Resolve the entrypoint function name (embedded default; optional file override).
    entrypoint = _resolve_entrypoint(task_id)
    if not entrypoint:
        return {
            "task_id": task_id,
            "passed": 0,
            "total": 0,
            "results": [],
            "success": False,
            "error": f"No entrypoint defined for task: {task_id}",
        }

    # SECURITY: Validate entrypoint against code-injection attacks.
    # Only allow valid Python identifiers.
    if not VALID_ENTRYPOINT_PATTERN.match(entrypoint):
        return {
            "task_id": task_id,
            "passed": 0,
            "total": 0,
            "results": [],
            "success": False,
            "error": f"Invalid entrypoint name: {entrypoint}. Must be a valid Python identifier (^[a-zA-Z_][a-zA-Z0-9_]*$)",
        }

    # Stage the candidate in a fresh scratch directory that is also the child's
    # cwd, so relative paths and module imports resolve inside the sandbox.
    sandbox_dir = tempfile.mkdtemp(prefix="bench-candidate-")
    temp_file = os.path.join(sandbox_dir, "candidate_solution.py")
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(solution_code)

    try:
        test_cases = HIDDEN_TEST_CASES[task_id]
        results = []
        passed_count = 0

        for i, test_case in enumerate(test_cases):
            # Extract input and expected output
            if len(test_case) == 2:
                if isinstance(test_case[0], tuple):
                    # Multiple arguments (only tuples indicate multiple args)
                    args = test_case[0]
                    expected = test_case[1]
                else:
                    # Single argument (lists are single args, not multiple)
                    args = (test_case[0],)
                    expected = test_case[1]
            else:
                args = test_case[:-1]
                expected = test_case[-1]

            # Build the execution command
            args_repr = ", ".join(
                json.dumps(arg) if isinstance(arg, (str, list)) else repr(arg)
                for arg in args
            )

            exec_code = (
                "import sys\n"
                f"sys.path.insert(0, {repr(str(Path(temp_file).parent))})\n"
                f"exec(open({repr(temp_file)}, encoding='utf-8').read())\n"
                f"result = {entrypoint}({args_repr})\n"
                "print(json.dumps(result))\n"
            )

            try:
                returncode, stdout, stderr, timed_out = _run_candidate(
                    [sys.executable, "-c", "import json\n" + exec_code],
                    timeout=TIMEOUT_SECONDS,
                    cwd=sandbox_dir,
                )

                if timed_out:
                    results.append({
                        "test": i,
                        "input": args,
                        "expected": expected,
                        "passed": False,
                        "error": f"Timeout after {TIMEOUT_SECONDS}s",
                    })
                elif returncode != 0:
                    # Syntax error, runtime exception, etc. surfaced via stderr.
                    results.append({
                        "test": i,
                        "input": args,
                        "expected": expected,
                        "passed": False,
                        "error": stderr.strip() or "Non-zero return code",
                    })
                else:
                    try:
                        actual = json.loads(stdout.strip())
                        passed = actual == expected
                        if passed:
                            passed_count += 1
                        results.append({
                            "test": i,
                            "input": args,
                            "expected": expected,
                            "actual": actual,
                            "passed": passed,
                        })
                    except json.JSONDecodeError:
                        results.append({
                            "test": i,
                            "input": args,
                            "expected": expected,
                            "passed": False,
                            "error": f"Output not JSON: {stdout.strip()}",
                        })
            except Exception as e:
                results.append({
                    "test": i,
                    "input": args,
                    "expected": expected,
                    "passed": False,
                    "error": f"Execution error: {e}",
                })

        return {
            "task_id": task_id,
            "passed": passed_count,
            "total": len(test_cases),
            "results": results,
            "success": passed_count == len(test_cases),
        }

    finally:
        # Remove the whole scratch directory (candidate file and anything it wrote)
        shutil.rmtree(sandbox_dir, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m bench.coding_grader <task_id> <solution_file>")
        sys.exit(1)

    task_id = sys.argv[1]
    solution_file = sys.argv[2]

    result = grade_solution(task_id, solution_file)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)
