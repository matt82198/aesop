#!/usr/bin/env python3
"""Fix all encoding violations."""

from pathlib import Path

def fix_files():
    """Fix encoding violations in all files."""

    violations = {
        'tools/ci_shard_runner.py': [174],
        'tools/claudemd_drift.py': [240],
        'tools/claudemd_sync_gate.py': [113],
        'tools/commit_lint.py': [91],
        'tools/crossos_drift.py': [59, 89, 116],
        'tools/eod_sweep.py': [67, 98, 116, 157, 219, 232],
        'tools/git_identity_check.py': [110, 124],
        'tools/handoff_proof.py': [184],
        'tools/health_score.py': [238, 245, 278],
        'tools/import_resolution_check.py': [31],
        'tools/integration_suite.py': [91],
        'tools/monitor_autohelp.py': [116],
        'tools/mcp_server.py': [139],
        'tools/node_runner.py': [82],
        'tools/portal_preview.py': [135],
        'tools/prepublish_scan.py': [30],
        'tools/scanner_selftest.py': [30, 41, 306],
        'tools/self_stats.py': [57],
        'tools/state_md_verifier.py': [32],
        'tools/tracker_autoclose.py': [144, 213],
        'tools/tracker_reconcile.py': [65, 89],
        'tools/verify_cost_panel.py': [187],
        'tools/verify_failure_drilldown.py': [55],
        'tools/verify_scorecards.py': [139],
        'tools/verify_test_coverage.py': [31],
        'tools/verify_test_suite_count.py': [33],
        'tools/wave_backlog_analyzer.py': [158, 190],
        'tools/wave_manifest_lint.py': [213, 233],
        'tools/wave_preflight.py': [211, 236],
        'driver/wave_loop.py': [504, 564],
    }

    fixed_count = 0

    for filepath_str, line_nums in violations.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            print(f"SKIP {filepath}: not found")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        modified = False

        for line_num in line_nums:
            idx = line_num - 1  # Convert to 0-indexed

            # Search for text=True or universal_newlines=True near this line
            found = False
            for search_idx in range(max(0, idx - 15), min(len(lines), idx + 10)):
                line = lines[search_idx]

                if ('text=True' in line or 'universal_newlines=True' in line) and 'encoding=' not in line:
                    # Check if this is part of a subprocess call
                    # Add encoding='utf-8' after text=True or universal_newlines=True

                    if 'text=True,' in line:
                        lines[search_idx] = line.replace(
                            'text=True,',
                            "text=True,\n" + " " * 16 + "encoding='utf-8',"
                        )
                        print(f"Fixed {filepath}:{line_num}")
                        modified = True
                        found = True
                        break
                    elif 'universal_newlines=True,' in line:
                        lines[search_idx] = line.replace(
                            'universal_newlines=True,',
                            "universal_newlines=True,\n" + " " * 16 + "encoding='utf-8',"
                        )
                        print(f"Fixed {filepath}:{line_num}")
                        modified = True
                        found = True
                        break

            if not found:
                print(f"SKIP {filepath}:{line_num}: pattern not found")

        if modified:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            fixed_count += 1

    print(f"\nFixed {fixed_count} files")

if __name__ == '__main__':
    fix_files()
