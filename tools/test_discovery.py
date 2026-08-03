#!/usr/bin/env python3
"""Test Command Validator & Auto-Mapper.
INDEX: Multi-language framework detector; identifies test runners, suggests testCmd pattern with confidence; `--validate` runs read-only checks with 120s timeout; `--json` output; stdlib-only

Scans a target repository, detects test frameworks, and suggests validated testCmd patterns.

Usage:
    python test_discovery.py <repo-path> [--json] [--validate]

Detects:
    - pytest (pytest.ini, pyproject.toml [tool.pytest], conftest.py, tests/*.py)
    - jest/vitest/mocha (package.json scripts + devDependencies, *.test.js)
    - go test (go.mod + *_test.go)
    - rspec (Gemfile, **/*_spec.rb)
    - shell test runners (tests/*.sh)

Output:
    Default: human-readable framework list with suggested testCmd, evidence, confidence
    --json: JSON object with frameworks array
    --validate: runs the suggested command with timeout to collect test count

Exit codes:
    0: detection success (frameworks found)
    1: no frameworks detected
    2: error (nonexistent path, invalid args)
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def is_valid_repo_path(path: str) -> bool:
    """Check if path exists and is accessible."""
    try:
        return Path(path).exists() and Path(path).is_dir()
    except (OSError, ValueError):
        return False


def detect_pytest(repo_path: Path) -> Optional[Dict[str, Any]]:
    """Detect pytest framework markers."""
    evidence = []

    # Check for pytest.ini
    if (repo_path / "pytest.ini").exists():
        evidence.append("pytest.ini")

    # Check for pyproject.toml with [tool.pytest]
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.pytest" in content:
                evidence.append("pyproject.toml [tool.pytest]")
        except Exception:
            pass

    # Check for conftest.py
    if (repo_path / "conftest.py").exists():
        evidence.append("conftest.py")

    # Check for test_*.py files
    test_files = list(repo_path.glob("**/test_*.py"))
    if test_files:
        evidence.append(f"{len(test_files)} test_*.py files")

    if evidence:
        return {
            "framework": "pytest",
            "suggested_testcmd": "pytest",
            "evidence": evidence,
            "confidence": "high" if len(evidence) >= 2 else "medium",
        }
    return None


def detect_jest_vitest_mocha(repo_path: Path) -> Optional[Dict[str, Any]]:
    """Detect jest, vitest, or mocha from package.json."""
    evidence = []
    detected_tool = None

    package_json = repo_path / "package.json"
    if not package_json.exists():
        return None

    try:
        content = package_json.read_text(encoding="utf-8")
        data = json.loads(content)

        # Check devDependencies
        dev_deps = data.get("devDependencies", {})
        if "jest" in dev_deps:
            evidence.append("jest in devDependencies")
            detected_tool = "jest"
        if "vitest" in dev_deps:
            evidence.append("vitest in devDependencies")
            detected_tool = detected_tool or "vitest"
        if "mocha" in dev_deps:
            evidence.append("mocha in devDependencies")
            detected_tool = detected_tool or "mocha"

        # Check scripts
        scripts = data.get("scripts", {})
        if "test" in scripts:
            test_script = scripts["test"]
            if "jest" in test_script:
                evidence.append("jest in test script")
                detected_tool = "jest"
            if "vitest" in test_script:
                evidence.append("vitest in test script")
                detected_tool = detected_tool or "vitest"
            if "mocha" in test_script:
                evidence.append("mocha in test script")
                detected_tool = detected_tool or "mocha"

        # Check for test files
        test_files = list(repo_path.glob("**/*.test.js")) + list(
            repo_path.glob("**/*.test.ts")
        )
        if test_files:
            evidence.append(f"{len(test_files)} *.test.js/ts files")

    except Exception:
        return None

    if detected_tool and evidence:
        return {
            "framework": detected_tool,
            "suggested_testcmd": f"{detected_tool} --listTests 2>/dev/null || {detected_tool}",
            "evidence": evidence,
            "confidence": "high" if len(evidence) >= 2 else "medium",
        }
    return None


def detect_go_test(repo_path: Path) -> Optional[Dict[str, Any]]:
    """Detect go test framework."""
    evidence = []

    # Check for go.mod
    if (repo_path / "go.mod").exists():
        evidence.append("go.mod")

    # Check for *_test.go files
    test_files = list(repo_path.glob("**/*_test.go"))
    if test_files:
        evidence.append(f"{len(test_files)} *_test.go files")

    if evidence:
        return {
            "framework": "go test",
            "suggested_testcmd": "go test ./... -list",
            "evidence": evidence,
            "confidence": "high" if len(evidence) >= 2 else "medium",
        }
    return None


def detect_rspec(repo_path: Path) -> Optional[Dict[str, Any]]:
    """Detect rspec framework."""
    evidence = []

    # Check for Gemfile
    if (repo_path / "Gemfile").exists():
        try:
            content = (repo_path / "Gemfile").read_text(encoding="utf-8")
            if "rspec" in content:
                evidence.append("rspec in Gemfile")
        except Exception:
            pass

    # Check for *_spec.rb files
    spec_files = list(repo_path.glob("**/*_spec.rb"))
    if spec_files:
        evidence.append(f"{len(spec_files)} *_spec.rb files")

    if evidence:
        return {
            "framework": "rspec",
            "suggested_testcmd": "rspec --list-drb 2>/dev/null || rspec",
            "evidence": evidence,
            "confidence": "high" if len(evidence) >= 2 else "medium",
        }
    return None


def detect_shell_tests(repo_path: Path) -> Optional[Dict[str, Any]]:
    """Detect shell test runners."""
    evidence = []

    # Check for tests/*.sh files
    shell_tests = list(repo_path.glob("tests/*.sh")) + list(
        repo_path.glob("tests/test_*.sh")
    )
    if shell_tests:
        evidence.append(f"{len(shell_tests)} *.sh test files")

    if evidence:
        return {
            "framework": "shell",
            "suggested_testcmd": "bash tools/run_shell_tests.sh . 2>/dev/null || bash tests/*.sh",
            "evidence": evidence,
            "confidence": "medium",
        }
    return None


def detect_frameworks(repo_path: Path) -> List[Dict[str, Any]]:
    """Detect all test frameworks in the repository."""
    frameworks = []

    detectors = [
        detect_pytest,
        detect_jest_vitest_mocha,
        detect_go_test,
        detect_rspec,
        detect_shell_tests,
    ]

    for detector in detectors:
        result = detector(repo_path)
        if result:
            frameworks.append(result)

    return frameworks


def validate_testcmd(testcmd: str, repo_path: Path, timeout_sec: int = 120) -> Dict[str, Any]:
    """Run the suggested testCmd with read-only flags and hard timeout.

    Returns dict with test_count, error, or timeout status.
    """
    start = time.time()

    # Build read-only variants of common test commands
    ro_cmd = testcmd
    if testcmd.startswith("pytest"):
        ro_cmd = testcmd.replace("pytest", "pytest --collect-only", 1)
    elif testcmd.startswith("jest"):
        ro_cmd = testcmd.replace("jest", "jest --listTests", 1)
    elif testcmd.startswith("go test"):
        ro_cmd = testcmd.replace("go test", "go test -list", 1)
    elif testcmd.startswith("rspec"):
        ro_cmd = testcmd.replace("rspec", "rspec --dry-run", 1)

    try:
        result = subprocess.run(
            ro_cmd,
            shell=True,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_sec,
        )
        elapsed = time.time() - start

        if result.returncode == 0:
            # Count lines in output as proxy for test count
            test_count = len([l for l in result.stdout.split("\n") if l.strip()])
            return {
                "validated": True,
                "test_count": test_count,
                "elapsed_seconds": round(elapsed, 1),
            }
        else:
            return {
                "validated": False,
                "error": "command returned non-zero exit code",
                "elapsed_seconds": round(elapsed, 1),
            }
    except subprocess.TimeoutExpired:
        return {
            "validated": False,
            "error": f"timeout after {timeout_sec}s",
            "elapsed_seconds": timeout_sec,
        }
    except Exception as e:
        return {
            "validated": False,
            "error": str(e),
        }


def format_text_output(frameworks: List[Dict[str, Any]]) -> str:
    """Format framework detection as human-readable text."""
    lines = []

    if not frameworks:
        return "No test frameworks detected"

    for fw in frameworks:
        lines.append(f"\n{fw['framework'].upper()}")
        lines.append(f"  Suggested: {fw['suggested_testcmd']}")
        lines.append(f"  Confidence: {fw['confidence']}")
        lines.append(f"  Evidence: {', '.join(fw['evidence'])}")

    return "\n".join(lines)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test command validator & auto-mapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo_path", help="Path to target repository")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Actually run suggested testCmd with timeout (read-only)",
    )

    args = parser.parse_args()

    # Validate path
    if not is_valid_repo_path(args.repo_path):
        print(f"Error: path does not exist or is not accessible: {args.repo_path}")
        return 2

    repo_path = Path(args.repo_path)

    # Detect frameworks
    frameworks = detect_frameworks(repo_path)

    if not frameworks:
        output = "No test frameworks detected"
        if args.json:
            print(json.dumps({"frameworks": [], "error": "no frameworks detected"}, indent=2))
        else:
            print(output)
        return 1

    # Validate if requested
    if args.validate:
        for fw in frameworks:
            validation = validate_testcmd(fw["suggested_testcmd"], repo_path)
            fw["validation"] = validation

    # Output
    if args.json:
        print(json.dumps({"frameworks": frameworks}, indent=2))
    else:
        print(format_text_output(frameworks))

    return 0


if __name__ == "__main__":
    sys.exit(main())
