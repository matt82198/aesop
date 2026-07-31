#!/usr/bin/env python3
"""Mechanical encoding fix tool - adds encoding='utf-8' to subprocess calls."""

import re
import sys
from pathlib import Path

VIOLATIONS = [
    ("tools/buildlog.py", 66),
    ("tools/chaos_harness.py", 323),
    ("tools/ci_merge_wait.py", 50),
    ("tools/ci_merge_wait.py", 257),
    ("tools/ci_shard_runner.py", 174),
    ("tools/claudemd_drift.py", 240),
    ("tools/claudemd_sync_gate.py", 113),
    ("tools/commit_lint.py", 91),
    ("tools/crossos_drift.py", 59),
    ("tools/crossos_drift.py", 89),
    ("tools/crossos_drift.py", 116),
    ("tools/eod_sweep.py", 67),
    ("tools/eod_sweep.py", 98),
    ("tools/eod_sweep.py", 116),
    ("tools/eod_sweep.py", 157),
    ("tools/eod_sweep.py", 219),
    ("tools/eod_sweep.py", 232),
    ("tools/git_identity_check.py", 110),
    ("tools/git_identity_check.py", 124),
    ("tools/handoff_proof.py", 184),
    ("tools/health_score.py", 238),
    ("tools/health_score.py", 245),
    ("tools/health_score.py", 278),
    ("tools/import_resolution_check.py", 31),
    ("tools/integration_suite.py", 91),
    ("tools/monitor_autohelp.py", 116),
    ("tools/mcp_server.py", 139),
    ("tools/node_runner.py", 82),
    ("tools/portal_preview.py", 135),
    ("tools/prepublish_scan.py", 30),
    ("tools/scanner_selftest.py", 30),
    ("tools/scanner_selftest.py", 41),
    ("tools/scanner_selftest.py", 306),
    ("tools/self_stats.py", 57),
    ("tools/state_md_verifier.py", 32),
    ("tools/tracker_autoclose.py", 144),
    ("tools/tracker_autoclose.py", 213),
    ("tools/tracker_reconcile.py", 65),
    ("tools/tracker_reconcile.py", 89),
    ("tools/verify_cost_panel.py", 187),
    ("tools/verify_failure_drilldown.py", 55),
    ("tools/verify_scorecards.py", 139),
    ("tools/verify_test_coverage.py", 31),
    ("tools/verify_test_suite_count.py", 33),
    ("tools/wave_backlog_analyzer.py", 158),
    ("tools/wave_backlog_analyzer.py", 190),
    ("tools/wave_manifest_lint.py", 213),
    ("tools/wave_manifest_lint.py", 233),
    ("tools/wave_preflight.py", 211),
    ("tools/wave_preflight.py", 236),
    ("driver/wave_loop.py", 504),
    ("driver/wave_loop.py", 564),
]

def fix_file(filepath, line_num):
    """Fix a single violation in a file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Find the subprocess.run/check_output/Popen call around line_num
        # Usually the call starts a few lines before the reported line
        start_search = max(0, line_num - 20)
        end_search = min(len(lines), line_num + 10)

        # Look for text=True or universal_newlines=True without encoding=
        found = False
        for i in range(start_search, end_search):
            if i < len(lines):
                line = lines[i]
                # Check if this line or nearby contains the issue
                if "text=True" in line or "universal_newlines=True" in line:
                    if "encoding=" not in line:
                        # This is the line to fix
                        # We need to find where to insert encoding='utf-8'
                        # It should go after text=True/universal_newlines=True
                        if "text=True" in line:
                            # Insert after text=True
                            lines[i] = line.replace("text=True,", "text=True,\n                encoding='utf-8',")
                        elif "universal_newlines=True" in line:
                            lines[i] = line.replace("universal_newlines=True,", "universal_newlines=True,\n                encoding='utf-8',")
                        found = True
                        break

        if found:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            print(f"Fixed {filepath}:{line_num}")
            return True
    except Exception as e:
        print(f"Error fixing {filepath}:{line_num}: {e}", file=sys.stderr)
        return False

    return False

def main():
    """Fix all violations."""
    fixed = 0
    for filepath, line_num in VIOLATIONS:
        full_path = Path(filepath)
        if full_path.exists():
            if fix_file(full_path, line_num):
                fixed += 1

    print(f"\nFixed {fixed} / {len(VIOLATIONS)} violations")

if __name__ == "__main__":
    main()
