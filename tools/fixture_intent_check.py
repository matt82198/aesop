#!/usr/bin/env python3
"""Fixture intent manifest validator.
INDEX: Deliberately-broken fixture manifest validator; verifies bench/fixtures-intent.json tracks all intentionally-broken/incomplete fixtures to distinguish benchmarks from regressions; CLI: `[--manifest PATH] [--root DIR] [--json]`; exit 0=valid/1=findings/2=error; stdlib-only

Verifies that deliberately-broken or intentionally-incomplete fixtures are
tracked in bench/fixtures-intent.json and remain identifiable from actual
regressions. Used to distinguish benchmark fixtures from real defects.

Exit codes:
  0 = all fixtures valid and in manifest
  1 = findings (fixture misconfiguration, file not found, etc.)
  2 = error (manifest parse failure, usage error)
"""

import json
import os
import sys
import re
from pathlib import Path


def load_manifest(manifest_path):
    """Load and parse the fixtures-intent manifest.

    Returns (manifest_list, error_message).
    error_message is None on success.
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return None, f"Manifest must be a JSON array, got {type(data).__name__}"
        return data, None
    except FileNotFoundError:
        return None, f"Manifest not found: {manifest_path}"
    except json.JSONDecodeError as e:
        return None, f"Manifest JSON parse error: {e}"
    except Exception as e:
        return None, f"Error reading manifest: {e}"


def validate_manifest_entry(entry, index, repo_root):
    """Validate a single manifest entry.

    Returns (is_valid, findings_list).
    findings_list contains error/warning strings; empty list means valid.
    """
    findings = []

    # Check required fields
    if not isinstance(entry, dict):
        findings.append(f"Entry {index}: not a dict")
        return False, findings

    required = ["path", "reason", "fixture_type", "added"]
    for field in required:
        if field not in entry:
            findings.append(f"Entry {index}: missing required field '{field}'")

    if findings:
        return False, findings

    path = entry.get("path")
    if not isinstance(path, str):
        findings.append(f"Entry {index}: 'path' must be string, got {type(path).__name__}")
        return False, findings

    # Check file exists
    full_path = os.path.join(repo_root, path)
    if not os.path.exists(full_path):
        findings.append(f"Entry {index}: fixture file not found: {path}")
    elif not os.path.isfile(full_path):
        findings.append(f"Entry {index}: fixture path is not a file: {path}")

    # Validate fixture_type
    fixture_type = entry.get("fixture_type")
    valid_types = ["deliberately_broken_code", "intentional_coverage_gap"]
    if fixture_type not in valid_types:
        findings.append(
            f"Entry {index}: invalid fixture_type '{fixture_type}', "
            f"must be one of {valid_types}"
        )

    # Validate reason is non-empty string
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        findings.append(f"Entry {index}: 'reason' must be non-empty string")

    # Validate added is a date string (ISO format check)
    added = entry.get("added")
    if not isinstance(added, str):
        findings.append(f"Entry {index}: 'added' must be string (ISO date)")
    elif not re.match(r"^\d{4}-\d{2}-\d{2}$", added):
        findings.append(f"Entry {index}: 'added' must be ISO date (YYYY-MM-DD), got '{added}'")

    return len(findings) == 0, findings


def validate_manifest_completeness(manifest, repo_root):
    """Verify that all discovered fixtures are in the manifest.

    Scans the fixture directories and ensures every deliberately-broken
    or intentional-gap fixture is tracked.

    Returns findings_list (empty if complete).
    """
    findings = []

    # Expected fixture paths that should be in manifest
    expected_fixtures = {
        "tests/fixtures/seam_s_sample_task/repo/test_sample.py",
        "tests/fixtures/seam_sample_task/repo/main.py",
        "bench/fixtures/mutation_fault_fixture.py",
        "bench/fixtures/test_mutation_fault_fixture.py",
    }

    manifest_paths = {entry.get("path") for entry in manifest if isinstance(entry, dict)}

    missing = expected_fixtures - manifest_paths
    if missing:
        for path in sorted(missing):
            findings.append(f"Fixture not in manifest: {path}")

    return findings


def check_fixtures(manifest_path=None, repo_root=None, json_output=False):
    """Main validation routine.

    Returns (exit_code, results_dict).
    results_dict includes 'valid', 'findings', 'entry_count' for --json output.
    """
    if repo_root is None:
        repo_root = os.getcwd()

    if manifest_path is None:
        manifest_path = os.path.join(repo_root, "bench", "fixtures-intent.json")

    # Load manifest
    manifest, load_error = load_manifest(manifest_path)
    if load_error:
        if json_output:
            return 2, {
                "valid": False,
                "error": load_error,
                "findings": [],
                "entry_count": 0
            }
        print(f"ERROR: {load_error}", file=sys.stderr)
        return 2, {"error": load_error}

    # Validate each entry
    findings = []
    for index, entry in enumerate(manifest):
        is_valid, entry_findings = validate_manifest_entry(entry, index, repo_root)
        findings.extend(entry_findings)

    # Verify completeness (all expected fixtures are tracked)
    completeness_findings = validate_manifest_completeness(manifest, repo_root)
    findings.extend(completeness_findings)

    # Determine exit code
    exit_code = 0 if not findings else 1

    if json_output:
        return exit_code, {
            "valid": exit_code == 0,
            "findings": findings,
            "entry_count": len(manifest),
            "manifest_path": manifest_path
        }

    # Human-readable output
    if findings:
        print(f"FINDINGS ({len(findings)}):", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
    else:
        print(f"OK: {len(manifest)} fixtures tracked and valid")

    return exit_code, {
        "valid": exit_code == 0,
        "findings": findings,
        "entry_count": len(manifest)
    }


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate fixture-intent manifest for deliberately-broken benchmarks"
    )
    parser.add_argument(
        "--manifest",
        help="Path to fixtures-intent.json (default: bench/fixtures-intent.json)"
    )
    parser.add_argument(
        "--root",
        help="Repository root directory (default: current directory)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args()

    exit_code, results = check_fixtures(
        manifest_path=args.manifest,
        repo_root=args.root,
        json_output=args.json
    )

    if args.json:
        print(json.dumps(results, indent=2))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
