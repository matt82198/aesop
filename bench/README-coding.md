# Coding Task Benchmark

## Overview

This benchmark measures **code correctness** by having models write Python functions to solve self-contained coding tasks, then grading them against hidden test cases that are external to the model.

## Honesty Rules

This benchmark differs from the judgment/extraction proxy benchmark in critical ways:

1. **Hidden Test Cases**: The grader defines objective test cases that are NOT shown in the task prompts. The model never sees these during task completion.

2. **External Grading**: Correctness is determined by isolated subprocess execution against concrete inputs/outputs, not by model judgment.

3. **Small N Caveat**: With only 8 tasks, this measures code-correctness narrowly. Results should not be extrapolated beyond small Python functions.

4. **Deterministic**: Unlike natural language tasks, code either runs correctly or it doesn't. There is no ambiguity in grading.

## Tasks

Each task is a small, self-contained Python function. Haiku is the target model for these tasks.

- **fizzbuzz**: Generate FizzBuzz sequence
- **is_palindrome**: Check if string is palindrome (case/space insensitive)
- **find_prime**: Check if integer is prime
- **reverse_string**: Reverse string without built-in methods
- **remove_duplicates**: Remove duplicates while preserving order
- **fibonacci_nth**: Get nth Fibonacci number
- **binary_search**: Implement binary search on sorted list
- **count_vowels**: Count vowels in a string

## Running the Benchmark

### 1. Generate Candidate Solutions

Run a model over `coding_tasks.jsonl` to generate solutions.

The task prompt tells the model to write a Python function with a specific name (entrypoint). For example:

```
Task: fizzbuzz
Entrypoint: fizzbuzz
Model must write a function: def fizzbuzz(n): ...
```

Save the model's complete output to a file.

### 2. Grade the Solution

```bash
python -m bench.coding_grader fizzbuzz solution.py
```

Output:
```json
{
  "task_id": "fizzbuzz",
  "passed": 15,
  "total": 15,
  "results": [...],
  "success": true
}
```

### 3. Interpret Results

- `success: true` means the model's function passed all hidden test cases
- `passed: X / total: Y` shows how many of the hidden cases passed
- `results[]` lists per-case verdicts (input, expected output, actual output, whether it passed)

## Implementation Notes

- **Execution isolation (what it does)**: Each candidate runs in its own subprocess with a 5-second timeout (the whole process group is killed on expiry). The child's environment is an allowlist (`PATH`, `PATHEXT`, `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `TEMP`, `TMP`, `TMPDIR`, plus `PYTHONIOENCODING`/`PYTHONDONTWRITEBYTECODE`), so `*_API_KEY`, `*_TOKEN`, `*_SECRET` and every other grader variable are NOT inherited. The candidate file is written to a fresh scratch directory that is also the child's working directory, and that directory is deleted afterwards.
- **Execution isolation (what it does NOT do)**: This is NOT a security sandbox. A candidate still runs as your user account and can read any file that account can read (including key or credential files on disk), open network connections, write anywhere it has permission, spawn processes, and consume unbounded memory or CPU within the timeout. Do not run untrusted candidates through this grader on a machine holding secrets you are not willing to expose to model-generated code. Environment scrubbing removes credentials from the environment only; it does not remove them from the filesystem.
- **Stdlib Only**: No external dependencies; uses only Python standard library
- **Windows-Safe**: Works on Windows, macOS, and Linux
- **JSON Protocol**: Test results and outputs are JSON for easy parsing

## Limitations

- This measures only small, deterministic Python functions
- It does not measure design patterns, readability, or performance
- Results are specific to the 8 tasks defined; generalizing beyond this set is not recommended
- All tasks have relatively small N (6-8 hidden test cases per task)

## Adding New Tasks

To add a new coding task:

1. Add entry to `coding_tasks.jsonl`: `{id, prompt, entrypoint}`
2. Add hidden test cases to `HIDDEN_TEST_CASES` in `coding_grader.py`
3. Add test cases to `tests/test_coding_grader.py`

Keep tasks self-contained and completable by Haiku.
