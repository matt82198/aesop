#!/usr/bin/env python3
"""
Guardrail G2: CI gate that verifies all on-disk test files are run by some CI job.

This tool prevents the "fake-green" bug class: test files that exist on disk but no
CI job/script runs them, making tests appear to pass when they never execute.

Discovers test files across all suites (Python, Node, Shell, Playwright) and verifies
each is covered by at least one runner.

Usage:
  python tools/verify_test_coverage.py --check     # Exit 1 if orphans found
  python tools/verify_test_coverage.py --fix       # Suggest how to add orphans
  python tools/verify_test_coverage.py --help      # Show this help

Exit codes:
  0: All tests covered
  1: Orphaned test files found
  2: Error running the tool
"""
import json
import re
import subprocess
import sys
from pathlib import Path


def find_git_tracked_files(pattern):
    """Find tracked files matching a git pattern using git ls-files."""
    try:
        result = subprocess.run(
            ["git", "ls-files", pattern],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=True,
        )
        # Normalize paths to forward slashes for consistency
        files = set(f.replace("\\", "/") for f in (result.stdout.strip().split("\n") if result.stdout.strip() else []))
        return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()


def find_disk_files(pattern):
    """Find files on disk matching a glob pattern."""
    repo_root = Path.cwd()
    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        return set()
    # Normalize paths to forward slashes for consistency with git ls-files
    return set(str(p.relative_to(repo_root)).replace("\\", "/") for p in tests_dir.glob(pattern))


def get_python_test_coverage():
    """Find Python test files that are covered by ci_shard_runner.py."""
    # ci_shard_runner.py uses: git ls-files tests/test_*.py
    return find_git_tracked_files("tests/test_*.py")


def get_node_test_coverage():
    """Find Node test files that are covered by npm test:node."""
    scripts = parse_package_json()
    test_node = scripts.get("test:node", "")

    # If there's no test:node script, no Node tests are covered
    if not test_node:
        return set()

    # Look for glob patterns in the script (e.g., tests/*.test.mjs)
    # Node.js test runner patterns: node --test tests/*.test.mjs
    covered = set()

    # Extract glob patterns like tests/*.test.mjs
    pattern_match = re.search(r"tests/\*\.test\.mjs", test_node)
    if pattern_match:
        # Match all *.test.mjs files in tests/
        covered = find_disk_files("*.test.mjs")

    return covered


def parse_package_json():
    """Parse package.json to extract test:sh script."""
    pkg_file = Path("package.json")
    if not pkg_file.exists():
        return {}
    try:
        pkg = json.loads(pkg_file.read_text())
        return pkg.get("scripts", {})
    except (json.JSONDecodeError, IOError):
        return {}


def get_shell_test_coverage():
    """Find shell test files that are covered by npm test:sh script.

    Handles both:
    - Legacy: explicit bash file references (e.g., bash tests/test_*.sh && bash ...)
    - New: glob runner invocation (bash tools/run_shell_tests.sh)
    """
    scripts = parse_package_json()
    test_sh = scripts.get("test:sh", "")

    covered = set()

    # Check if test:sh delegates to the glob runner
    if "bash tools/run_shell_tests.sh" in test_sh or "bash ./tools/run_shell_tests.sh" in test_sh:
        # The glob runner discovers tests matching: *.test.sh, test_*.sh, test-*.sh
        # Replicate its discovery in Python (cross-platform, no bash dependency)
        covered.update(find_disk_files("*.test.sh"))
        covered.update(find_disk_files("test_*.sh"))
        covered.update(find_disk_files("test-*.sh"))
        return covered

    # Legacy parsing: extract explicit bash file references
    # Example: bash tests/test_*.sh && bash tests/test-*.sh && bash tests/backup-fleet.test.sh
    bash_pattern = r"bash\s+([^\s&|]+)"
    for match in re.finditer(bash_pattern, test_sh):
        file_path = match.group(1).strip()
        if "*" in file_path:
            pattern = file_path.split("/")[-1]
            if "test_*.sh" in pattern:
                covered.update(find_disk_files("test_*.sh"))
            elif "test-*.sh" in pattern:
                covered.update(find_disk_files("test-*.sh"))
            elif "*.test.sh" in pattern:
                covered.update(find_disk_files("*.test.sh"))
        else:
            covered.add(file_path)

    return covered


def parse_playwright_config():
    """Parse playwright.config.ts to extract testMatch pattern."""
    config_file = Path("playwright.config.ts")
    if not config_file.exists():
        # Default pattern if no config
        return ["*.spec.ts"]

    try:
        content = config_file.read_text()
        # Look for testMatch: 'pattern' or testMatch: "pattern"
        match = re.search(r"testMatch\s*:\s*['\"]([^'\"]+)['\"]", content)
        if match:
            return [match.group(1)]
        # Default pattern
        return ["*.spec.ts"]
    except IOError:
        return ["*.spec.ts"]


def get_playwright_test_coverage():
    """Find Playwright test files that are covered by playwright.config.ts."""
    patterns = parse_playwright_config()
    covered = set()
    for pattern in patterns:
        covered.update(find_disk_files(pattern))
    return covered


def get_all_test_files():
    """Discover all test files on disk."""
    all_tests = {
        "python": find_disk_files("test_*.py"),
        "node": find_disk_files("*.test.mjs"),
        "shell": find_disk_files("test_*.sh") | find_disk_files("test-*.sh") | find_disk_files("*.test.sh"),
        "playwright": find_disk_files("*.spec.ts"),
    }
    return all_tests


def get_all_coverage():
    """Get all test files that are covered by some runner."""
    return {
        "python": get_python_test_coverage(),
        "node": get_node_test_coverage(),
        "shell": get_shell_test_coverage(),
        "playwright": get_playwright_test_coverage(),
    }


def find_orphans():
    """Find test files that exist on disk but are not covered by any runner."""
    all_tests = get_all_test_files()
    coverage = get_all_coverage()

    orphans = {}
    for test_type, files in all_tests.items():
        covered = coverage[test_type]
        orphaned = files - covered
        if orphaned:
            orphans[test_type] = sorted(orphaned)

    return orphans


def format_fix_suggestion(test_type, file_path):
    """Format a suggestion for how to add an orphaned test to the runners."""
    if test_type == "python":
        # For Python, tests are auto-discovered by ci_shard_runner.py via git ls-files
        return f"Add to git: git add {file_path}"
    elif test_type == "node":
        # For Node, they're auto-discovered by npm test:node (tests/*.test.mjs)
        return f"Ensure file matches tests/*.test.mjs pattern (already matched: {file_path})"
    elif test_type == "shell":
        # For shell, add to package.json test:sh script
        return f"Add to package.json test:sh: bash {file_path} &&"
    elif test_type == "playwright":
        # For Playwright, they're auto-discovered by playwright.config.ts testMatch
        return f"Ensure file matches playwright.config.ts testMatch pattern (already matched: {file_path})"
    return f"Unknown test type: {test_type}"


def check_coverage(verbose=False):
    """Check if all test files are covered. Returns True if all covered, False otherwise."""
    orphans = find_orphans()

    if not orphans:
        if verbose:
            print("All test files are covered by CI runners.")
        return True

    # Report orphans
    print("ERROR: Found orphaned test files not covered by any CI runner:")
    print()
    for test_type, files in orphans.items():
        print(f"{test_type.upper()} tests ({len(files)}):")
        for file_path in files:
            print(f"  - {file_path}")
    print()

    return False


def suggest_fixes():
    """Suggest how to add orphaned test files to the runners."""
    orphans = find_orphans()

    if not orphans:
        print("All test files are covered by CI runners.")
        return

    print("Suggestions for adding orphaned tests to CI runners:")
    print()
    for test_type, files in orphans.items():
        print(f"{test_type.upper()} tests:")
        for file_path in files:
            suggestion = format_fix_suggestion(test_type, file_path)
            print(f"  {file_path}: {suggestion}")
    print()


def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        success = check_coverage(verbose=True)
        sys.exit(0 if success else 1)

    if len(sys.argv) > 1 and sys.argv[1] == "--fix":
        suggest_fixes()
        sys.exit(0)

    # Default to --check
    success = check_coverage(verbose=True)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
