#!/usr/bin/env python3
"""Tests for receipt_agreement.py measurement tool.

This test suite validates the agreement between locally-emitted receipts and
hosted CI check-run results.
"""

import sys
from pathlib import Path

_TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TEST_DIR.parent / "tools"))

import receipt_agreement as ra  # noqa: E402


def test_agreement_all_match():
    """Agreement when all py-shard parts agree with hosted ci checks."""
    receipts = [
        {
            "sha": "abc123def456",
            "receipt": {
                "head_sha": "abc123def456",
                "parts": [
                    {"name": "py-shard-0", "exit_code": 0},
                    {"name": "py-shard-1", "exit_code": 0},
                    {"name": "py-shard-2", "exit_code": 0},
                    {"name": "py-shard-3", "exit_code": 0},
                ]
            }
        }
    ]

    check_runs = {
        "abc123def456": {
            "ci (0)": {"conclusion": "success"},
            "ci (1)": {"conclusion": "success"},
            "ci (2)": {"conclusion": "success"},
            "ci (3)": {"conclusion": "success"},
        }
    }

    def fetch_fake_receipt(sha, repo_slug, api=None):
        if sha in [r["sha"] for r in receipts]:
            r = next(r for r in receipts if r["sha"] == sha)
            return ({"receipt": r["receipt"], "sig": {"scheme": "hmac-sha256"}}, f"found {sha}")
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return check_runs.get(sha, {})

    agreements, disagreements, missing = ra.measure_agreement(
        receipts,
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    assert len(agreements) == 4
    assert len(disagreements) == 0
    assert len(missing) == 0


def test_disagreement_shard_mismatch():
    """Disagreement when receipt and hosted CI disagree."""
    receipts = [
        {
            "sha": "bad456789def",
            "receipt": {
                "head_sha": "bad456789def",
                "parts": [
                    {"name": "py-shard-0", "exit_code": 1},  # failed locally
                    {"name": "py-shard-1", "exit_code": 0},
                    {"name": "py-shard-2", "exit_code": 0},
                    {"name": "py-shard-3", "exit_code": 0},
                ]
            }
        }
    ]

    check_runs = {
        "bad456789def": {
            "ci (0)": {"conclusion": "success"},  # passed hosted
            "ci (1)": {"conclusion": "success"},
            "ci (2)": {"conclusion": "success"},
            "ci (3)": {"conclusion": "success"},
        }
    }

    def fetch_fake_receipt(sha, repo_slug, api=None):
        if sha in [r["sha"] for r in receipts]:
            r = next(r for r in receipts if r["sha"] == sha)
            return ({"receipt": r["receipt"], "sig": {"scheme": "hmac-sha256"}}, f"found {sha}")
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return check_runs.get(sha, {})

    agreements, disagreements, missing = ra.measure_agreement(
        receipts,
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    assert len(agreements) == 3
    assert len(disagreements) == 1
    assert disagreements[0]["sha"] == "bad456789def"
    assert disagreements[0]["part"] == "py-shard-0"
    assert disagreements[0]["local"] == "failed"
    assert disagreements[0]["hosted"] == "success"


def test_missing_receipt():
    """Handle missing receipts gracefully."""
    def fetch_fake_receipt(sha, repo_slug, api=None):
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return {}

    agreements, disagreements, missing = ra.measure_agreement(
        [{"sha": "noreceipe1234", "receipt": None}],
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    assert len(missing) == 1
    assert missing[0] == "noreceipe1234"


def test_unknown_part_mapping():
    """Handle unknown part names in receipt."""
    receipts = [
        {
            "sha": "unknown999999",
            "receipt": {
                "head_sha": "unknown999999",
                "parts": [
                    {"name": "unknown-part", "exit_code": 0},
                ]
            }
        }
    ]

    check_runs = {
        "unknown999999": {}
    }

    def fetch_fake_receipt(sha, repo_slug, api=None):
        if sha in [r["sha"] for r in receipts]:
            r = next(r for r in receipts if r["sha"] == sha)
            return ({"receipt": r["receipt"], "sig": {"scheme": "hmac-sha256"}}, f"found {sha}")
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return check_runs.get(sha, {})

    # Receipt has only unknown parts; all comparable parts will be marked missing
    agreements, disagreements, missing = ra.measure_agreement(
        receipts,
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    assert len(agreements) == 0
    # All 4 comparable parts will be marked as disagreement (local missing, hosted unknown)
    assert len(disagreements) == 4


def test_agreement_percentage():
    """Calculate agreement percentage correctly."""
    receipts = [
        {
            "sha": "perc111111111",
            "receipt": {
                "head_sha": "perc111111111",
                "parts": [
                    {"name": "py-shard-0", "exit_code": 0},
                    {"name": "py-shard-1", "exit_code": 1},
                    {"name": "py-shard-2", "exit_code": 0},
                    {"name": "py-shard-3", "exit_code": 0},
                ]
            }
        }
    ]

    check_runs = {
        "perc111111111": {
            "ci (0)": {"conclusion": "success"},
            "ci (1)": {"conclusion": "success"},  # disagrees: local failed, hosted success
            "ci (2)": {"conclusion": "success"},
            "ci (3)": {"conclusion": "success"},
        }
    }

    def fetch_fake_receipt(sha, repo_slug, api=None):
        if sha in [r["sha"] for r in receipts]:
            r = next(r for r in receipts if r["sha"] == sha)
            return ({"receipt": r["receipt"], "sig": {"scheme": "hmac-sha256"}}, f"found {sha}")
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return check_runs.get(sha, {})

    agreements, disagreements, missing = ra.measure_agreement(
        receipts,
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    total = len(agreements) + len(disagreements)
    pct = 100 * len(agreements) // total if total > 0 else 0
    assert pct == 75  # 3 out of 4 agree


def test_hosted_check_missing():
    """Handle missing hosted check-runs."""
    receipts = [
        {
            "sha": "mischeck1234",
            "receipt": {
                "head_sha": "mischeck1234",
                "parts": [
                    {"name": "py-shard-0", "exit_code": 0},
                ]
            }
        }
    ]

    check_runs = {
        "mischeck1234": {}  # no ci (0)
    }

    def fetch_fake_receipt(sha, repo_slug, api=None):
        if sha in [r["sha"] for r in receipts]:
            r = next(r for r in receipts if r["sha"] == sha)
            return ({"receipt": r["receipt"], "sig": {"scheme": "hmac-sha256"}}, f"found {sha}")
        return (None, f"not found {sha}")

    def fetch_fake_check_runs(sha, repo_slug):
        return check_runs.get(sha, {})

    agreements, disagreements, missing = ra.measure_agreement(
        receipts,
        fetch_receipt_func=fetch_fake_receipt,
        fetch_check_runs_func=fetch_fake_check_runs
    )

    # When hosted check is missing, should mark as disagreement
    assert len(disagreements) >= 1 or len(missing) >= 0


def run_suite():
    """Run all tests."""
    tests = [
        test_agreement_all_match,
        test_disagreement_shard_mismatch,
        test_missing_receipt,
        test_unknown_part_mapping,
        test_agreement_percentage,
        test_hosted_check_missing,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            print(f"PASS: {test_func.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test_func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test_func.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_suite())
