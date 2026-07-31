#!/usr/bin/env python3
"""
NO-UNVERIFIED-METRICS gate: scan git diff for hard numeric claims in *.md files
that lack source verification.

A hard numeric claim is:
  - Percentages (e.g., "42%")
  - Multipliers (e.g., "3x", "3×")
  - Dollar amounts (e.g., "$15000")

Exclusions (don't require verification):
  - Version numbers (e.g., "v1.2.3", "2.0")
  - Dates (e.g., "2024", "2025")
  - Line numbers (e.g., "line 42", "line:123")
  - Numbers in code/JSON contexts

A claim passes verification if:
  - The same line or an adjacent line has: <!-- metrics-verified: <source> -->
  - OR the claim itself looks like a version (e.g., "v1.2.3")
  - OR the claim is a year/date (2000-2999)
  - OR the claim is a line reference (line X, :X, @X patterns)

Usage:
  metrics_gate.py [origin/main...HEAD]
    Scans ADDED lines in diff from origin/main...HEAD (default).
    Exits 0 if all metrics are verified or excluded.
    Exits 1 if unverified hard claims found (lists them).

Used in CI as a PR gate:
  metrics_gate.py origin/main...HEAD
"""

import sys
import subprocess
import re
from pathlib import Path
from typing import List, Tuple, Optional


class MetricsGate:
    """Scan git diffs for unverified hard numeric claims."""

    # Regex patterns for hard numeric claims
    PERCENTAGE_PATTERN = re.compile(r'\d+\s*%')
    MULTIPLIER_PATTERN = re.compile(r'\d+\s*[x×]\s*(?:faster|slower|more|less|bigger|smaller|better|worse|improvement|increase|decrease|growth)')
    DOLLAR_PATTERN = re.compile(r'\$\s*\d+')

    # Exclusion patterns
    VERSION_PATTERN = re.compile(r'\bv?\d+\.\d+(?:\.\d+)?(?:[.-](?:alpha|beta|rc|a|b)\d*)?\b')
    DATE_PATTERN = re.compile(r'\b(19|20)\d{2}\b')
    LINE_REFERENCE_PATTERN = re.compile(r'(?:line\s*|:|@)\s*\d+')

    # Verification marker pattern
    # Non-greedy .+? rather than [^-]+ : the old class rejected any source citation
    # containing a hyphen or dash ("wave-1", "PR-123", "2026-07-31"), which is most
    # real citations, so valid markers silently failed to register.
    VERIFICATION_PATTERN = re.compile(r'<!--\s*metrics-verified:\s*.+?-->', re.DOTALL)

    def __init__(self, diff_range: str = "origin/main...HEAD"):
        """Initialize with a git diff range.

        The gate scans the git repository at the current working directory
        (like git itself), not the directory the script happens to live in.
        In CI it is invoked from the repo root, so behaviour is unchanged;
        this also makes the gate exercisable against any repo under test.
        """
        self.diff_range = diff_range
        self.repo_root = Path.cwd()

    def get_diff_lines(self) -> List[Tuple[str, str]]:
        """Get lines added in diff. Returns list of (file, line_content)."""
        try:
            # Use git diff to get added lines
            # Restrict the diff to markdown (the only files this gate scans) so
            # binary blobs never reach the decoder, and pin the encoding: text=True
            # alone falls back to the locale codec (cp1252 on Windows) and dies on
            # any non-cp1252 byte.
            result = subprocess.run(
                ["git", "diff", self.diff_range, "--unified=0", "--", "*.md"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode == 0:
                # Success, continue to parse
                pass
            elif result.returncode == 128:
                # Invalid range (ref doesn't exist) - no diff available
                return []
            else:
                # Other git error - fail closed (cannot verify => deny)
                print(f"ERROR: git diff failed with exit code {result.returncode}", file=sys.stderr)
                sys.exit(1)

            diff_output = result.stdout
            added_lines = []
            current_file = None

            for line in diff_output.split('\n'):
                # Track current file
                if line.startswith('+++'):
                    current_file = line[6:]  # Strip "+++ b/"
                    if current_file.startswith('b/'):
                        current_file = current_file[2:]
                # Capture added lines (start with '+' but not '+++')
                elif line.startswith('+') and not line.startswith('+++'):
                    content = line[1:]  # Strip leading '+'
                    if current_file and current_file.endswith('.md'):
                        added_lines.append((current_file, content))

            return added_lines
        except Exception as e:
            # Unexpected error (file I/O, parsing) - fail closed (cannot verify => deny)
            print(f"ERROR: git diff operation failed: {e}", file=sys.stderr)
            sys.exit(1)

    def is_version_number(self, text: str) -> bool:
        """Check if text looks like a version number."""
        return bool(self.VERSION_PATTERN.search(text))

    def is_date(self, text: str) -> bool:
        """Check if text looks like a date (year)."""
        return bool(self.DATE_PATTERN.search(text))

    def is_line_reference(self, text: str) -> bool:
        """Check if text looks like a line reference."""
        return bool(self.LINE_REFERENCE_PATTERN.search(text))

    def is_excluded(self, text: str) -> bool:
        """Check if a claim is excluded from verification."""
        return (
            self.is_version_number(text)
            or self.is_date(text)
            or self.is_line_reference(text)
        )

    def find_hard_claims(self, text: str) -> List[str]:
        """Find hard numeric claims in text."""
        claims = []
        # Strip verification markers before scanning: a citation is evidence, not a
        # new claim. Without this, sourcing "78.9%" inside the marker itself raises
        # a fresh unverified-metric finding and the claim can never be satisfied.
        text = self.VERIFICATION_PATTERN.sub("", text)
        # Find percentages
        claims.extend(self.PERCENTAGE_PATTERN.findall(text))
        # Find multipliers
        claims.extend(self.MULTIPLIER_PATTERN.findall(text))
        # Find dollar amounts
        claims.extend(self.DOLLAR_PATTERN.findall(text))
        return claims

    def is_verified(self, lines: List[Tuple[str, int, str]]) -> bool:
        """Check if a claim line has verification marker nearby.

        lines: list of (file, line_num, content) for this and adjacent lines
        """
        for _, _, content in lines:
            if self.VERIFICATION_PATTERN.search(content):
                return True
        return False

    def check(self) -> Tuple[int, List[str]]:
        """Check for unverified metrics. Returns (exit_code, list of failures)."""
        diff_lines = self.get_diff_lines()
        failures = []

        # Group lines by file for context
        lines_by_file = {}
        for file, content in diff_lines:
            if file not in lines_by_file:
                lines_by_file[file] = []
            lines_by_file[file].append(content)

        # Check each added line
        for file, contents in lines_by_file.items():
            for i, content in enumerate(contents):
                claims = self.find_hard_claims(content)
                for claim in claims:
                    # Skip if it's excluded (version, date, line ref)
                    if self.is_excluded(claim):
                        continue

                    # Check if verified (current line or adjacent)
                    context = []
                    if i > 0:
                        context.append((file, i - 1, contents[i - 1]))
                    context.append((file, i, content))
                    if i + 1 < len(contents):
                        context.append((file, i + 1, contents[i + 1]))

                    if not self.is_verified(context):
                        failures.append(
                            f"{file}: unverified metric '{claim}' — add "
                            f"<!-- metrics-verified: <source> --> on same or adjacent line"
                        )

        exit_code = 1 if failures else 0
        return exit_code, failures


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "diff_range",
        nargs="?",
        default="origin/main...HEAD",
        help="Git diff range to scan (default: origin/main...HEAD)",
    )
    args = parser.parse_args()

    gate = MetricsGate(args.diff_range)
    exit_code, failures = gate.check()

    if failures:
        print("NO-UNVERIFIED-METRICS GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        sys.exit(exit_code)
    else:
        print("NO-UNVERIFIED-METRICS: OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
