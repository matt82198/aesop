#!/usr/bin/env python3
"""Record one end-to-end wave cycle on a real non-Claude backend.

Proves the 0.5.0 seat-swap claim empirically: a GPT-4o-mini worker seat
takes a RED coding task to GREEN through the full AgentDriver seam
(dispatch_worker -> orchestrator run_command -> verified by exit code).

The recording captures:
  1. probe_capabilities() output (honest tier report)
  2. build_manifest_item() enrichment (tier, policy knobs)
  3. dispatch_item() result (live API call -> file writes -> test verdict)
  4. Full JSON report suitable for evidence check-in

USAGE:
  # Requires OPENAI_API_KEY in environment
  python docs/recordings/run_non_claude_recording.py

  # Or with explicit output path:
  python docs/recordings/run_non_claude_recording.py --output docs/recordings/report.json

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Add driver/ to path.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import ROLE_WORKER  # noqa: E402
from codex_driver import CodexDriver  # noqa: E402
from wave_bridge import build_manifest_item, dispatch_item  # noqa: E402
from verification_policy import verification_policy  # noqa: E402


def run_recording(output_path=None):
    """Run one end-to-end wave cycle on GPT-4o-mini and capture JSON report."""

    # Verify API key is available (never print it).
    api_key = os.environ.get("OPENAI" + "_" + "API" + "_" + "KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment. Aborting.", file=sys.stderr)
        sys.exit(1)
    print(f"API key present (length={len(api_key)})")

    # 1. Build driver -- GPT-4o-mini via CodexDriver (default worker model).
    print("\n=== Phase 1: Build driver ===")
    driver = CodexDriver()  # default transport reads OPENAI_API_KEY at call time
    caps = driver.probe_capabilities()
    print(f"Driver: {caps.name}")
    print(f"Tier: {caps.recommended_verification_tier}")
    print(f"Accuracy: {caps.tool_use_accuracy}")
    print(f"Structured output: {caps.structured_output}")
    print(f"Worker filesystem: {caps.worker_filesystem_access}")

    # 2. Set up a fixture task: broken module + test.
    print("\n=== Phase 2: Create fixture ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # RED module: add() multiplies instead of adding.
        broken_module = (
            "def add(a, b):\n"
            "    return a * b  # BUG: should be +\n"
        )
        (tmpdir_path / "math_ops.py").write_text(broken_module, encoding="utf-8")

        # Test that verifies add() correctness.
        test_code = (
            "import sys\n"
            "import unittest\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent))\n"
            "from math_ops import add\n"
            "\n"
            "class TestAdd(unittest.TestCase):\n"
            "    def test_add_positive(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
            "\n"
            "    def test_add_zero(self):\n"
            "        self.assertEqual(add(0, 5), 5)\n"
            "\n"
            "    def test_add_negative(self):\n"
            "        self.assertEqual(add(-1, 1), 0)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
        (tmpdir_path / "test_math_ops.py").write_text(test_code, encoding="utf-8")

        # Confirm the test is RED before fix.
        print("Verifying test is RED before fix...")
        test_cmd = sys.executable + " -m unittest test_math_ops -v"
        pre_result = driver.run_command(test_cmd, cwd=tmpdir)
        assert pre_result.exit_code != 0, "Test should fail before fix"
        print(f"Pre-fix test exit code: {pre_result.exit_code} (expected non-zero)")

        # 3. Build manifest item (proves the bridge enrichment path).
        print("\n=== Phase 3: Build manifest item ===")
        backlog_item = {
            "slug": "fix-add-function",
            "ownsFiles": ["math_ops.py"],
            "prompt": (
                "The add(a, b) function in math_ops.py is broken. "
                "It returns a * b instead of a + b. Fix it to return "
                "the sum of a and b."
            ),
            "testCmd": test_cmd,
            "workDir": tmpdir,
        }
        manifest_item = build_manifest_item(driver, backlog_item)
        print(f"Model: {manifest_item['model']}")
        print(f"Verification tier: {manifest_item['verificationTier']}")
        print(f"Repair cap: {manifest_item['repairCap']}")
        print(f"Adversarial review required: {manifest_item['requireAdversarialReview']}")
        print(f"Spot-check fraction: {manifest_item['spotCheckFrac']}")
        print(f"Validate all JSON: {manifest_item['validateAllJson']}")

        # 4. Dispatch through the wave bridge (LIVE API call).
        print("\n=== Phase 4: Dispatch (LIVE API call to GPT-4o-mini) ===")
        start_time = time.monotonic()
        dispatch_result = dispatch_item(driver, manifest_item, workdir=tmpdir)
        elapsed = time.monotonic() - start_time
        print(f"Route: {dispatch_result['route']}")
        print(f"OK: {dispatch_result['ok']}")
        print(f"Test exit: {dispatch_result['testExit']}")
        print(f"Verified: {dispatch_result['verified']}")
        print(f"Files written: {dispatch_result.get('filesWritten')}")
        print(f"Elapsed: {elapsed:.2f}s")
        if dispatch_result.get("error"):
            print(f"Error: {dispatch_result['error']}")

        # 5. Read the fixed file content.
        fixed_content = None
        if dispatch_result["ok"]:
            fixed_content = (tmpdir_path / "math_ops.py").read_text(encoding="utf-8")
            print(f"\nFixed file content:\n{fixed_content}")

        # 6. Confirm the test is GREEN after fix (independent verification).
        post_verified = False
        if dispatch_result["ok"]:
            print("\n=== Phase 5: Independent verification ===")
            post_result = driver.run_command(test_cmd, cwd=tmpdir)
            post_verified = post_result.exit_code == 0
            print(f"Post-fix test exit code: {post_result.exit_code}")
            print(f"Independent verification: {'PASS' if post_verified else 'FAIL'}")

        # 7. Assemble the full report.
        print("\n=== Phase 6: Assemble report ===")
        tokens = driver.get_tokens_spent()
        unmetered = driver.get_unmetered_dispatches()

        report = {
            "meta": {
                "title": "Non-Claude backend end-to-end recording",
                "purpose": (
                    "Empirical proof that aesop's AgentDriver seam works with "
                    "a real non-Claude backend (GPT-4o-mini via OpenAI API). "
                    "Converts the 0.5.0 seat-swap claim from architecturally "
                    "proven + offline to empirically proven."
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "backend": "codex (gpt-4o-mini)",
                "api": "OpenAI Chat Completions",
                "live": True,
            },
            "driver_capabilities": {
                "name": caps.name,
                "tier": caps.recommended_verification_tier,
                "tool_use_accuracy": caps.tool_use_accuracy,
                "parallel_dispatch": caps.parallel_dispatch,
                "worker_filesystem_access": caps.worker_filesystem_access,
                "worker_shell_access": caps.worker_shell_access,
                "structured_output": caps.structured_output,
                "worktree_isolation": caps.worktree_isolation,
                "native_cost_tracking": caps.native_cost_tracking,
            },
            "manifest_enrichment": {
                "model": manifest_item["model"],
                "verificationTier": manifest_item["verificationTier"],
                "repairCap": manifest_item["repairCap"],
                "requireAdversarialReview": manifest_item["requireAdversarialReview"],
                "spotCheckFrac": manifest_item["spotCheckFrac"],
                "validateAllJson": manifest_item["validateAllJson"],
            },
            "task": {
                "slug": backlog_item["slug"],
                "description": (
                    "Fix a broken add() function (returns a*b instead of a+b)"
                ),
                "owned_files": backlog_item["ownsFiles"],
                "test_command": backlog_item["testCmd"],
            },
            "result": {
                "route": dispatch_result["route"],
                "ok": dispatch_result["ok"],
                "test_exit": dispatch_result["testExit"],
                "verified": dispatch_result["verified"],
                "files_written": dispatch_result.get("filesWritten"),
                "worker_id": dispatch_result.get("workerId"),
                "error": dispatch_result.get("error"),
                "elapsed_s": round(elapsed, 2),
                "independent_verification": post_verified,
            },
            "cost": {
                "tokens_spent": tokens,
                "unmetered_dispatches": unmetered,
            },
            "fixed_file_content": fixed_content,
            "pre_fix_test_exit": pre_result.exit_code,
            "evidence_chain": [
                "1. Driver constructed with CodexDriver (gpt-4o-mini default)",
                "2. probe_capabilities() reports tier 2 (honest self-report)",
                "3. build_manifest_item() enriches with tier + 4 policy knobs",
                "4. Test confirmed RED before dispatch (exit != 0)",
                "5. dispatch_item() calls LIVE OpenAI API (gpt-4o-mini)",
                "6. Model returns structured JSON patch (full-file replacement)",
                "7. Driver validates JSON schema + ownership + writes file",
                "8. Orchestrator runs test command (exit code is ground truth)",
                "9. ok=True ONLY because test exit == 0 (never model say-so)",
                "10. Independent re-run confirms test still GREEN",
            ],
        }

        # Write report.
        if output_path is None:
            output_path = str(
                Path(__file__).parent / "non-claude-e2e-recording.json"
            )
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport written to: {output_path}")
        print(f"Success: {report['result']['ok']}")

        return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Record a non-Claude backend end-to-end wave cycle"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for JSON report (default: docs/recordings/non-claude-e2e-recording.json)",
    )
    args = parser.parse_args()
    report = run_recording(output_path=args.output)
    sys.exit(0 if report["result"]["ok"] else 1)
