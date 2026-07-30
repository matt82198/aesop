#!/usr/bin/env python3
"""Regression gate validator — ensures audit/regression dispatches use the actual CI gate.

Root cause escape (esc-regression-lens-wrong-gate): audit/regression agents ran
`pytest tests/` (a development proxy) instead of `tools/ci_shard_runner.py`
(the actual CI gate defined in .github/workflows/ci.yml). The dispatch prompt
had no explicit constraint requiring the exact gate command. The agent also
reported "7+ test failures" inferred from truncated progress output after its own
300s timeout (exit 124), without validating that collection completed or that
exit codes indicated real failures.

Guardrail G8: This linter scans driver/*.py, monitor/*.py, and refinesystem
prompts for regression/verification/audit dispatch keywords. Extracts cited test
commands from prompts. Validates against repo config: ci_shard_runner.py must be
cited for Python tests, never pytest tests/ or generic proxies. Detects timeout
exit codes (124) and flags them as incomplete collection (not failure evidence).

Modes:
  regression_gate_validator.py --check [PATH]    Exit 1 if violations found
  regression_gate_validator.py --json [PATH]      Output violations as JSON
  regression_gate_validator.py [PATH]             Default: check mode on cwd

Suppression:
  Add '# regression-gate-ok' on the line with a violation to suppress it.

Exit: 0=clean, 1=violations found, 2=error
"""

import argparse
import ast
import json
import re
import sys
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# Keywords indicating regression/verification/audit dispatch context
REGRESSION_KEYWORDS = [
    r"\b(regression|verification|audit)\b",
    r"\b(run.*test|execute.*test)\b",
    r"\b(pytest|test.*command|ci.*shard)\b",
    r"\bexit.*code\b",
]

# Forbidden test patterns (development proxies, wrong gates)
FORBIDDEN_TEST_PATTERNS = {
    "pytest_invocation": {
        "pattern": r'(?:[\[\",\']pytest[\]\",\']|subprocess\..*pytest|run.*pytest)',
        "description": "pytest invocation detected; must use ci_shard_runner.py instead",
        "fix": "Use: python tools/ci_shard_runner.py [shard_id] [total_shards]",
        "why": "ci_shard_runner.py is the authoritative CI gate with proper sharding",
    },
    "python_unittest": {
        "pattern": r"\bpython\s+-m\s+unittest\s+tests",
        "description": "python -m unittest is a development proxy",
        "fix": "Use: python tools/ci_shard_runner.py [shard_id] [total_shards]",
        "why": "ci_shard_runner.py is the authoritative CI gate",
    },
    "timeout_exit_code": {
        "pattern": r"(?:==|=|\s)\s*124\b|result\.returncode\s*==\s*124|exit.*124",
        "description": "Exit code 124 (timeout) indicates incomplete collection, not test failures",
        "fix": "Never infer test failures from exit 124; increase timeout or debug collection issues",
        "why": "Timeout exit codes prove nothing about tests; the gate must complete to validate results",
    },
}

# Suppression comment
SUPPRESSION_COMMENT = "# regression-gate-ok"


def is_regression_context_file(content: str) -> bool:
    """Check if file contains regression/verification/audit dispatch keywords."""
    for keyword_pattern in REGRESSION_KEYWORDS:
        if re.search(keyword_pattern, content, re.IGNORECASE):
            return True
    return False


def check_suppression(line: str) -> bool:
    """Check if line has suppression comment."""
    return SUPPRESSION_COMMENT in line or "// regression-gate-ok" in line


def extract_code_from_prompt(prompt_text: str) -> List[str]:
    """Extract code blocks from a prompt string."""
    code_blocks = []
    # Extract backtick code blocks
    code_blocks.extend(re.findall(r"```(?:python|sh|bash)?\s*\n(.*?)\n```", prompt_text, re.DOTALL))
    # Extract inline code
    code_blocks.extend(re.findall(r"`([^`]+)`", prompt_text))
    return code_blocks


def is_pure_comment_line(line: str) -> bool:
    """Check if entire line is a comment (not code with a trailing comment)."""
    stripped = line.strip()
    return stripped.startswith("#") or stripped.startswith("//")


def find_violations(file_path: Path, content: str) -> List[Dict]:
    """Find regression gate violations in a file."""
    violations = []

    # Don't scan files that don't contain regression context
    if not is_regression_context_file(content):
        return violations

    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        # Skip suppressed lines
        if check_suppression(line):
            continue

        # Skip pure comment lines (but not lines with trailing comments)
        if is_pure_comment_line(line):
            continue

        # Check against forbidden patterns
        for pattern_key, pattern_info in FORBIDDEN_TEST_PATTERNS.items():
            if re.search(pattern_info["pattern"], line, re.IGNORECASE):
                # Skip pytest patterns in docstrings
                if pattern_key == "pytest_invocation" and ('"""' in line or "'''" in line):
                    continue

                # Additional filter for patterns that commonly have false positives
                if pattern_key == "timeout_exit_code":
                    # Only flag if line looks like code, not documentation
                    if any(cond in line for cond in ["if ", " == ", " = ", "result", "returncode"]):
                        violations.append({
                            "file": str(file_path),
                            "line": line_num,
                            "pattern": pattern_key,
                            "description": pattern_info["description"],
                            "fix": pattern_info["fix"],
                            "why": pattern_info["why"],
                            "code": line.strip()[:100],
                        })
                else:
                    # Other patterns: add without additional filtering
                    violations.append({
                        "file": str(file_path),
                        "line": line_num,
                        "pattern": pattern_key,
                        "description": pattern_info["description"],
                        "fix": pattern_info["fix"],
                        "why": pattern_info["why"],
                        "code": line.strip()[:100],
                    })

    return violations


def scan_directory(start_path: Path, recursive: bool = True) -> Tuple[Dict[str, List], List[str]]:
    """Scan directory for regression gate violations.

    Returns: (violations_by_file, errors)
    """
    violations_by_file = {}
    errors = []

    if start_path.is_file():
        # Single file
        try:
            with open(start_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            violations = find_violations(start_path, content)
            if violations:
                violations_by_file[str(start_path)] = violations
        except Exception as e:
            errors.append(f"Error reading {start_path}: {e}")
        return violations_by_file, errors

    # Directory scan
    if not start_path.is_dir():
        errors.append(f"Path does not exist: {start_path}")
        return violations_by_file, errors

    # Scan driver/, monitor/, and refinesystem prompt patterns
    scan_patterns = [
        "driver/*.py",
        "monitor/*.py",
        "skills/**/*.md",  # SKILL.md files often contain prompts
        "*.md",  # Markdown files that might contain dispatch prompts
    ]

    for pattern in scan_patterns:
        for file_path in start_path.glob(pattern):
            if file_path.is_file():
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    violations = find_violations(file_path.relative_to(start_path), content)
                    if violations:
                        violations_by_file[str(file_path.relative_to(start_path))] = violations
                except Exception as e:
                    errors.append(f"Error reading {file_path}: {e}")

    return violations_by_file, errors


def format_violations(violations_by_file: Dict[str, List]) -> str:
    """Format violations for human-readable output."""
    if not violations_by_file:
        return "No regression gate violations found."

    output = []
    total_violations = sum(len(v) for v in violations_by_file.values())
    output.append(f"Found {total_violations} regression gate violation(s):")
    output.append("")

    for file_path, violations in violations_by_file.items():
        output.append(f"{file_path}:")
        for v in violations:
            output.append(f"  Line {v['line']}: {v['pattern']}")
            output.append(f"    Description: {v['description']}")
            output.append(f"    Why: {v['why']}")
            output.append(f"    Fix: {v['fix']}")
            output.append(f"    Code: {v['code']}")
            output.append("")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Regression gate validator — ensures regression/audit dispatches use the actual CI gate"
    )
    parser.add_argument("path", nargs="?", default=".", help="Path to scan (default: current directory)")
    parser.add_argument(
        "--check", action="store_true", default=True, help="Exit 1 if violations found (default mode)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output violations as JSON"
    )

    args = parser.parse_args()

    start_path = Path(args.path).resolve()

    violations_by_file, errors = scan_directory(start_path)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)

    if args.json:
        # JSON output
        output = {
            "violations": violations_by_file,
            "total": sum(len(v) for v in violations_by_file.values()),
            "errors": errors,
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        print(format_violations(violations_by_file))

    # Exit codes
    if errors and not violations_by_file:
        sys.exit(2)  # Error
    elif violations_by_file:
        sys.exit(1)  # Violations found
    else:
        sys.exit(0)  # Clean


if __name__ == "__main__":
    main()
