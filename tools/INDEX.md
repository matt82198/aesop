<!-- GENERATED-BY: tools/gen_tool_index.py -->
# tools/ index (generated -- do not hand-edit)

One-line purpose per tool, collected from each tool's `INDEX:` docstring line.
Regenerate with `python tools/gen_tool_index.py --regenerate`; a new tool is
listed by adding an `INDEX: <one-liner>` line to its module docstring/header.

- `gen_suite_counts.py` -- Live suite-count library (no stored artifact): derives Node/Shell/Python test-suite counts straight from `git ls-files` on every call; stdlib-only, deterministic, ASCII-safe; counts never derive to zero (fail-closed). `--json` (default, read-only) prints the live counts; there is no `--check`/`--regenerate` mode and nothing is written anywhere, because as of structural fix #830 there is no committed tests/SUITE-COUNTS.json left to drift or regenerate -- counts are a pure function of the tree, computed fresh on every call, so nothing can go stale. Counts are derived per UNIQUE path, not per `git ls-files` line: an unmerged path is listed once per index stage (1=base/2=ours/3=theirs), and one file can match two shell globs at once, so a naive line-count over-counts (bit PRs #710/#711 under the old tests/CLAUDE.md-line gate); `list_git_files()` collects into a Python set to fix both. A merge in progress (MERGE_HEAD set) is a loud, non-fatal stderr `[WARN]` rather than a refusal, since conflict resolution is exactly when counts legitimately move. Consumed by `tools/verify_test_suite_count.py` (CI-shard-coverage gate) and `tests/test_list_test_suites.py` / `tests/test_tests_claudemd_drift.py` as the live ground truth.
- `gen_tool_index.py` -- Generated tool-index builder; walks `git ls-files tools/`, extracts each tool's `INDEX:` docstring/header line, emits sorted tools/INDEX.md between GENERATED-BY markers; modes `--check` (byte-compare, exit 1 + regenerate hint) / `--regenerate` / `--json`; a scanned tool with NO `INDEX:` line FAILS CLOSED (exit 1) so a new tool cannot land undocumented; deterministic + ASCII-safe; stdlib-only.

<!-- END-GENERATED -->
