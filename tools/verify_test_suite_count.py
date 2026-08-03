#!/usr/bin/env python3
"""
Verify test suite counts in tests/CLAUDE.md match actual test files on disk.

Read-only validation vs. regeneration are strictly separated:

- --check (default) / --strict: READ-ONLY validation. Never writes anything.
  Verifies the three suite-count label lines are present exactly once each, that
  their counts parse as integers, and that they match the counts derived from
  `git ls-files`. Any mismatch is a FAILURE with a "run --regenerate" hint.
- --regenerate (alias: --fix) [--dry-run]: the ONLY writing mode. Rewrites the
  documented counts in tests/CLAUDE.md to match the files on disk, in the
  canonical `**<Label> (N suites)**:` form, one line per label.

Count-line scanning is deliberately hardened against format-variant evasion:

- Line-anchored and tolerant of spacing and colon placement, so `**Python (N
  suites)** :` and `**Python (N suites):**` are recognized as count lines and
  cannot slip past the exactly-one assertion.
- Labels must be exactly `Node`/`Shell`/`Python` in ASCII. A count-line-shaped
  line carrying a look-alike label (e.g. a Cyrillic `o`) is reported as MALFORMED
  rather than being silently invisible.
- Fenced code blocks (``` / ~~~) and HTML comments are masked out before matching,
  so a documented format example is never a duplicate AND never satisfies the
  exactly-one requirement on its own. The writing mode rewrites only the real
  line, never a fenced example.

--strict is currently an exact alias for --check; it exists so CI can be wired to
a main-only strict invocation later without changing the tool's contract again.

Exit codes:
    0  counts match (check) / regeneration succeeded
    1  drift, missing label line, duplicated label line, or usage conflict
    2  cannot evaluate (file missing, unparseable count, or a vacuous zero-file
       derivation while CLAUDE.md documents non-zero counts -- fail-closed)

Usage:
    python tools/verify_test_suite_count.py --check [--repo ROOT]
    python tools/verify_test_suite_count.py --strict [--repo ROOT]
    python tools/verify_test_suite_count.py --regenerate [--dry-run] [--repo ROOT]

Read-only and writing modes are mutually exclusive; if neither is specified,
defaults to --check. Idempotent: running --regenerate twice produces identical
results, and --check never changes the tree at all.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple


# Force UTF-8 output on all platforms (especially Windows where stdout defaults to cp1252)
if sys.stdout.encoding and 'utf' not in sys.stdout.encoding.lower():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, TypeError):
        # Python < 3.7 doesn't have reconfigure; fall back to arrow-free output
        pass
if sys.stderr.encoding and 'utf' not in sys.stderr.encoding.lower():
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, TypeError):
        pass


# The three suite labels, in validation/reporting order. ASCII only, deliberately:
# a label containing a Unicode homoglyph must be reported as MALFORMED rather than
# silently skipped (the U+043E Cyrillic-o evasion).
LABELS = ("Node", "Shell", "Python")

# Canonical count line, e.g. `**Python (227 suites)**:`
CANONICAL_TEMPLATE = "**{label} ({count} suites)**:"

# Tolerant count-line matcher. Anchored to the start of a line, and accepting the
# two colon placements seen in the wild (`...**:` and `...:**`) plus incidental
# whitespace, so a format variant is still recognized as a count line instead of
# slipping past the exactly-one assertion.
COUNT_LINE_RE = re.compile(
    r"(?m)^[ \t]*"
    r"(?P<core>\*\*\s*(?P<label>" + "|".join(LABELS) + r")\s*"
    r"\(\s*(?P<count>\d+)\s+suites?\s*\)\s*"
    r"(?:\*\*\s*:|:\s*\*\*))"
)

# Loose matcher used only to flag lines that LOOK like count lines but whose label
# is not one of the exact ASCII labels above (homoglyphs, typos, renamed sections).
SUSPECT_LINE_RE = re.compile(
    r"(?m)^[ \t]*\*\*\s*(?P<label>[^(*\n]+?)\s*\(\s*\d+\s+suites?\s*\)\s*(?:\*\*\s*:|:\s*\*\*)"
)

# Regions that are documentation ABOUT count lines, not count lines themselves.
FENCE_RE = re.compile(r"(?ms)^[ \t]*(?P<fence>```|~~~).*?^[ \t]*(?P=fence)[ \t]*$")
HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")

REGENERATE_HINT = "Run: python tools/verify_test_suite_count.py --regenerate"


def mask_non_content(content: str) -> str:
    """Blank out fenced code blocks and HTML comments, preserving every offset.

    Masked characters become spaces (newlines kept) so match spans found against
    the masked text address the identical positions in the original text. This
    makes fenced format examples and commented-out lines invisible to the scanner
    in BOTH directions: they never count as duplicates, and they can never satisfy
    the exactly-one requirement on their own.
    """
    chars = list(content)

    for regex in (FENCE_RE, HTML_COMMENT_RE):
        for match in regex.finditer(content):
            for i in range(match.start(), match.end()):
                if chars[i] != "\n":
                    chars[i] = " "

    return "".join(chars)


def find_count_lines(content: str):
    """Return {label: [match, ...]} for real (unmasked) count lines.

    Match spans are valid against the ORIGINAL content string.
    """
    masked = mask_non_content(content)
    found = {label: [] for label in LABELS}
    for match in COUNT_LINE_RE.finditer(masked):
        found[match.group("label")].append(match)
    return found


def find_suspect_labels(content: str):
    """Return count-line-shaped lines whose label is not an exact ASCII label."""
    masked = mask_non_content(content)
    suspects = []
    for match in SUSPECT_LINE_RE.finditer(masked):
        label = match.group("label").strip()
        if label not in LABELS:
            suspects.append(label)
    return suspects


class StructureError(Exception):
    """A tests/CLAUDE.md structural problem, carrying the process exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def count_git_files(*patterns: str) -> int:
    """Count files matching patterns using git ls-files.

    Omits untracked files; uses git to ensure we count only tracked files.
    """
    count = 0
    for pattern in patterns:
        try:
            result = subprocess.run(
                ["git", "ls-files", pattern],
                capture_output=True,
                text=True,
                encoding='utf-8',
                check=True,
                timeout=10,
            )
            count += len([line for line in result.stdout.strip().split("\n") if line])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return count


def get_actual_counts(repo_root: Path) -> Tuple[int, int, int]:
    """Get actual test suite counts from disk.

    Returns: (node_count, shell_count, python_count)
    """
    node_count = count_git_files("tests/*.test.mjs")
    shell_count = count_git_files("tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh")
    python_count = count_git_files("tests/test_*.py")

    return node_count, shell_count, python_count


def validate_structure(content: str):
    """Validate structure and return {label: match} for the single real count line each.

    Fail-closed on a count-line-shaped line with a non-ASCII/unknown label
    (MALFORMED), on a missing OR duplicated count line for any suite type
    (exactly-one assertion), and on a count literal that will not parse.

    Raises:
        StructureError: code 1 for malformed/missing/duplicated lines, code 2 for
            unparseable count literals.
    """
    suspects = find_suspect_labels(content)
    if suspects:
        rendered = ", ".join(repr(s) for s in sorted(set(suspects)))
        raise StructureError(
            f"[FAIL] MALFORMED suite count line(s) in tests/CLAUDE.md: unrecognized "
            f"label(s) {rendered}. Labels must be exactly one of "
            f"{', '.join(LABELS)} (ASCII); a look-alike character makes the count "
            f"line invisible to this gate. Expected: "
            f"{CANONICAL_TEMPLATE.format(label='Python', count='N')}",
            1,
        )

    found = find_count_lines(content)
    resolved = {}

    for label in LABELS:
        matches = found[label]

        if len(matches) == 0:
            raise StructureError(
                f"[FAIL] Missing {label} test suite section in tests/CLAUDE.md. "
                f"Expected: {CANONICAL_TEMPLATE.format(label=label, count='N')} "
                "(count lines inside a ``` fence or an HTML comment do not count)",
                1,
            )
        if len(matches) > 1:
            raise StructureError(
                f"[FAIL] Found {len(matches)} duplicated "
                f"{CANONICAL_TEMPLATE.format(label=label, count='N')} "
                "count lines in tests/CLAUDE.md. Only one is allowed.",
                1,
            )

        try:
            int(matches[0].group("count"))
        except ValueError:
            raise StructureError(
                f"[ERROR] {label} suite count in tests/CLAUDE.md is not a parseable "
                f"integer: {matches[0].group('count')!r}",
                2,
            )

        resolved[label] = matches[0]

    return resolved


def extract_documented_counts(content: str) -> Tuple[int, int, int]:
    """Validate structure and return the documented (node, shell, python) counts."""
    resolved = validate_structure(content)
    return tuple(int(resolved[label].group("count")) for label in LABELS)


def assert_evaluable(documented: Tuple[int, int, int], actual: Tuple[int, int, int]) -> None:
    """Fail-closed on a vacuous zero-file derivation (git broken / not a repo).

    Raises:
        StructureError: code 2 when git reports zero files everywhere while
            CLAUDE.md documents non-zero counts.
    """
    if actual == (0, 0, 0) and documented != (0, 0, 0):
        raise StructureError(
            "[ERROR] Cannot evaluate: git ls-files returned zero files, but CLAUDE.md "
            "documents non-zero counts. This indicates a git configuration problem or "
            "the tool is running outside a git repository.",
            2,
        )


def check_mode(claudemd_path: Path) -> int:
    """READ-ONLY validation of tests/CLAUDE.md suite counts. Never writes.

    Args:
        claudemd_path: Path to tests/CLAUDE.md

    Returns:
        0 if counts match, 1 if drift / missing / duplicated count lines,
        2 if the state cannot be evaluated.
    """
    content = claudemd_path.read_text(encoding="utf-8")

    try:
        documented = extract_documented_counts(content)
        actual = get_actual_counts(claudemd_path.parent.parent)
        assert_evaluable(documented, actual)
    except StructureError as e:
        print(str(e), file=sys.stderr)
        return e.code

    if documented == actual:
        print("[OK] Test suite counts match")
        return 0

    doc_node, doc_shell, doc_python = documented
    act_node, act_shell, act_python = actual

    print("[DRIFT] Test suite count mismatch in tests/CLAUDE.md:")
    if doc_node != act_node:
        print(f"  Node: CLAUDE.md says {doc_node}, actual is {act_node}")
    if doc_shell != act_shell:
        print(f"  Shell: CLAUDE.md says {doc_shell}, actual is {act_shell}")
    if doc_python != act_python:
        print(f"  Python: CLAUDE.md says {doc_python}, actual is {act_python}")
    print("")
    print(REGENERATE_HINT)
    print("Then commit the updated tests/CLAUDE.md with your change.")
    return 1


def regenerate_mode(claudemd_path: Path, dry_run: bool = False) -> int:
    """Rewrite counts in tests/CLAUDE.md to match actual files. The only writing mode.

    Args:
        claudemd_path: Path to tests/CLAUDE.md
        dry_run: If True, show what would change but don't write

    Returns:
        0 if successful (or dry_run shows what would change), 1 on structural
        error, 2 if the state cannot be evaluated.
    """
    content = claudemd_path.read_text(encoding="utf-8")

    try:
        # Same exactly-one assertion as --check: a structurally invalid document is
        # never rewritten, so this path can no longer launder a duplicated count
        # line into a file the gate would reject.
        resolved = validate_structure(content)
        documented = tuple(int(resolved[label].group("count")) for label in LABELS)
        actual = get_actual_counts(claudemd_path.parent.parent)
        # Never zero out a real document because git could not be read.
        assert_evaluable(documented, actual)
    except StructureError as e:
        print(str(e), file=sys.stderr)
        return e.code

    by_label = dict(zip(LABELS, actual))

    # Rewrite exactly the one real count line per label, by span, in reverse order
    # so earlier offsets stay valid. Fenced examples and commented-out lines were
    # masked out of the scan and are therefore never touched.
    updated = content
    for match in sorted(resolved.values(), key=lambda m: m.start("core"), reverse=True):
        label = match.group("label")
        replacement = CANONICAL_TEMPLATE.format(label=label, count=by_label[label])
        start, end = match.span("core")
        updated = updated[:start] + replacement + updated[end:]

    if updated == content:
        print("[OK] Counts already match, no changes needed")
        return 0

    doc_node, doc_shell, doc_python = documented
    act_node, act_shell, act_python = actual

    if dry_run:
        print("[DRY-RUN] Would update counts:")
        print(f"  Node: {doc_node} -> {act_node}")
        print(f"  Shell: {doc_shell} -> {act_shell}")
        print(f"  Python: {doc_python} -> {act_python}")
        print("")
        print("Run without --dry-run to apply changes.")
        return 0

    claudemd_path.write_text(updated, encoding="utf-8")

    print("[REGENERATED] Updated tests/CLAUDE.md:")
    print(f"  Node: {act_node} suites")
    print(f"  Shell: {act_shell} suites")
    print(f"  Python: {act_python} suites")
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: verify counts match; exit 1 on drift. Never writes. Default mode.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Alias for --check (reserved for future main-only CI wiring)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite counts in tests/CLAUDE.md to match actual files (the only writing mode)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Deprecated alias for --regenerate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --regenerate: show what would change but don't write (implies --regenerate)",
    )
    parser.add_argument(
        "--claudemd",
        type=Path,
        default=None,
        help="Path to tests/CLAUDE.md (default: auto-detect from repo root)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    read_only = args.check or args.strict
    write = args.regenerate or args.fix

    # Validate mutually exclusive modes
    if read_only and write:
        print(
            "[ERROR] read-only mode (--check/--strict) and writing mode "
            "(--regenerate/--fix) are mutually exclusive",
            file=sys.stderr,
        )
        return 1

    # --dry-run implies the writing mode
    if args.dry_run and not write:
        write = True

    # Default to read-only if neither specified
    if not read_only and not write:
        read_only = True

    # Determine repo root
    repo_root = args.repo or Path.cwd()
    repo_root = repo_root.resolve()

    # Determine CLAUDE.md path
    if args.claudemd:
        claudemd_path = args.claudemd.resolve()
    else:
        claudemd_path = repo_root / "tests" / "CLAUDE.md"

    if not claudemd_path.exists():
        print(f"[ERROR] {claudemd_path} not found", file=sys.stderr)
        return 2

    # Run the selected mode
    if read_only:
        return check_mode(claudemd_path)
    return regenerate_mode(claudemd_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
