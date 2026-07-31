#!/usr/bin/env python3
"""Batch fix encoding violations in subprocess calls."""

import re
from pathlib import Path

def fix_encoding_violations():
    """Fix all encoding violations found by encoding_lint.py."""

    violations = {
        "tools/buildlog.py": [66],
        "tools/chaos_harness.py": [323],
        "tools/ci_merge_wait.py": [50, 257],
        "tools/ci_shard_runner.py": [174],
        "tools/claudemd_drift.py": [240],
        "tools/claudemd_sync_gate.py": [113],
        "tools/commit_lint.py": [91],
        "tools/crossos_drift.py": [59, 89, 116],
        "tools/eod_sweep.py": [67, 98, 116, 157, 219, 232],
        "tools/git_identity_check.py": [110, 124],
        "tools/handoff_proof.py": [184],
        "tools/health_score.py": [238, 245, 278],
        "tools/import_resolution_check.py": [31],
        "tools/integration_suite.py": [91],
        "tools/monitor_autohelp.py": [116],
        "tools/mcp_server.py": [139],
        "tools/node_runner.py": [82],
        "tools/portal_preview.py": [135],
        "tools/prepublish_scan.py": [30],
        "tools/scanner_selftest.py": [30, 41, 306],
        "tools/self_stats.py": [57],
        "tools/state_md_verifier.py": [32],
        "tools/tracker_autoclose.py": [144, 213],
        "tools/tracker_reconcile.py": [65, 89],
        "tools/verify_cost_panel.py": [187],
        "tools/verify_failure_drilldown.py": [55],
        "tools/verify_scorecards.py": [139],
        "tools/verify_test_coverage.py": [31],
        "tools/verify_test_suite_count.py": [33],
        "tools/wave_backlog_analyzer.py": [158, 190],
        "tools/wave_manifest_lint.py": [213, 233],
        "tools/wave_preflight.py": [211, 236],
        "driver/wave_loop.py": [504, 564],
    }

    total_fixed = 0
    for filepath_str, line_nums in violations.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            print(f"SKIP: {filepath} not found")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')

        # For each violation line, find the subprocess.run call and add encoding
        modified = False
        for line_num in line_nums:
            idx = line_num - 1  # Convert to 0-indexed

            # Search around the line for the subprocess call
            # Usually it spans multiple lines, so we need to find text=True or universal_newlines=True
            for search_idx in range(max(0, idx - 30), min(len(lines), idx + 10)):
                if 'text=True' in lines[search_idx] or 'universal_newlines=True' in lines[search_idx]:
                    # Check if encoding= is already present in this or next few lines
                    has_encoding = False
                    for check_idx in range(search_idx, min(len(lines), search_idx + 5)):
                        if 'encoding=' in lines[check_idx]:
                            has_encoding = True
                            break

                    if not has_encoding:
                        # Add encoding after text=True or universal_newlines=True
                        if 'text=True,' in lines[search_idx]:
                            lines[search_idx] = lines[search_idx].replace(
                                'text=True,',
                                "text=True,\n" + " " * 16 + "encoding='utf-8',"
                            )
                        elif 'text=True\n' in lines[search_idx]:
                            lines[search_idx] = lines[search_idx].replace(
                                'text=True\n',
                                "text=True,\n" + " " * 16 + "encoding='utf-8'\n"
                            )
                        elif 'universal_newlines=True,' in lines[search_idx]:
                            lines[search_idx] = lines[search_idx].replace(
                                'universal_newlines=True,',
                                "universal_newlines=True,\n" + " " * 16 + "encoding='utf-8',"
                            )
                        modified = True
                        print(f"Fixed {filepath}:{line_num}")
                        break

        if modified:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            total_fixed += 1

    print(f"\nModified {total_fixed} files")

if __name__ == '__main__':
    fix_encoding_violations()
