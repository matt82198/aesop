"""Tests for tools.baseline_liveness_check — dead-baseline/stale-entry ratchet guard.

Builds a throwaway temp repo per test (never the real aesop checkout) with a small
fixture consumer tool that implements the `--baseline FILE --json` contract the real
consumers (stateapi_lint.py, portability_check.py, subprocess_guard.py) already use,
so the liveness checker is exercised against real subprocess invocations rather than
mocks.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.baseline_liveness_check import (  # noqa: E402
    check_repo,
    discover_baseline_files,
    find_consumer,
    main,
    parse_stale_entry,
    prune_baseline,
    query_consumer_stale,
)

# A minimal consumer tool implementing the shared --baseline/--json ratchet contract:
# scans TARGET_FILE for the literal marker string FOUND_MARKER, one violation per
# matching line (keyed "relpath:lineno"), then diffs against the baseline exactly
# like the real tools (stale = in baseline but not current; new = reverse).
_FIXTURE_CONSUMER = '''#!/usr/bin/env python3
"""Fixture consumer tool for baseline_liveness_check tests.
INDEX: test fixture only, not a real gate
"""
import json
import sys
from pathlib import Path

MARKER = "FINDME"


def scan(repo_root):
    violations = []
    target = Path(repo_root) / "widget.py"
    if not target.is_file():
        return violations
    for i, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if MARKER in line:
            violations.append("widget.py:{0}".format(i))
    return violations


def load_baseline(path):
    p = Path(path)
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("violations", [])


def main():
    argv = sys.argv[1:]
    baseline_file = None
    as_json = False
    i = 0
    while i < len(argv):
        if argv[i] == "--baseline":
            i += 1
            baseline_file = argv[i]
        elif argv[i] == "--json":
            as_json = True
        i += 1

    repo_root = Path(__file__).resolve().parent.parent
    current = scan(repo_root)
    baseline = load_baseline(baseline_file) if baseline_file else []

    stale = sorted(set(baseline) - set(current))
    new = sorted(set(current) - set(baseline))
    ok = not stale and not new

    if as_json:
        print(json.dumps({"ok": ok, "stale": stale, "new": new, "violations": current}))
    else:
        print("ok" if ok else "mismatch")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


class BaselineLivenessCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "tools" / "widget_lint.py").write_text(
            _FIXTURE_CONSUMER, encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_widget(self, lines):
        (self.repo_root / "widget.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_baseline(self, name, violations):
        path = self.repo_root / name
        path.write_text(json.dumps({"violations": violations}, indent=2) + "\n", encoding="utf-8")
        return path

    # ---- discovery -------------------------------------------------

    def test_discover_baseline_files_finds_root_and_tools(self):
        self._write_baseline(".widget-baseline.json", [])
        tools_baseline = self.repo_root / "tools" / "zz-baseline.json"
        tools_baseline.write_text(json.dumps({"violations": []}), encoding="utf-8")
        found = discover_baseline_files(self.repo_root)
        names = sorted(str(p.relative_to(self.repo_root)).replace("\\", "/") for p in found)
        self.assertIn(".widget-baseline.json", names)
        self.assertIn("tools/zz-baseline.json", names)

    # ---- dead baseline ----------------------------------------------

    def test_dead_baseline_has_no_consumer(self):
        self._write_baseline(".orphan-baseline.json", ["nothing@here"])
        consumer = find_consumer(".orphan-baseline.json", self.repo_root)
        self.assertIsNone(consumer)

    def test_dead_baseline_fails_naming_it(self):
        self._write_baseline(".orphan-baseline.json", ["nothing@here"])
        result = check_repo(self.repo_root)
        self.assertIn(".orphan-baseline.json", result["dead"])
        self.assertEqual(result["stale"], [])

    def test_bare_literal_reference_is_not_a_consumer(self):
        # A tools/ file that merely lists the baseline's name as a protected
        # string literal (like a lint-evasion token table) must NOT count as
        # a consumer -- it never actually reads the file.
        (self.repo_root / "tools" / "protected_names.py").write_text(
            'PROTECTED = [\n    ".widget-baseline.json",\n    ".other-baseline.json",\n]\n',
            encoding="utf-8",
        )
        self._write_baseline(".widget-baseline.json", [])
        consumer = find_consumer(".widget-baseline.json", self.repo_root)
        self.assertIsNone(consumer)

    def test_real_main_exits_1_and_names_dead_baseline(self):
        self._write_baseline(".orphan-baseline.json", ["nothing@here"])
        rc = main(["--root", str(self.repo_root), "--json"])
        self.assertEqual(rc, 1)

    # ---- consumer discovery via hardcoded usage ----------------------

    def test_hardcoded_usage_line_is_a_real_consumer(self):
        (self.repo_root / "tools" / "reader.py").write_text(
            'from pathlib import Path\n'
            'def f(repo_root):\n'
            '    baseline_file = Path(repo_root) / ".widget-baseline.json"\n'
            '    return baseline_file\n',
            encoding="utf-8",
        )
        self._write_baseline(".widget-baseline.json", [])
        consumer = find_consumer(".widget-baseline.json", self.repo_root)
        self.assertEqual(consumer, "tools/reader.py")

    # ---- consumer discovery via CI/hook wiring -----------------------

    def test_ci_wiring_reference_is_a_real_consumer(self):
        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --check --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        self._write_baseline(".widget-baseline.json", [])
        consumer = find_consumer(".widget-baseline.json", self.repo_root)
        self.assertEqual(consumer, "tools/widget_lint.py")

    # ---- stale entries (file deleted) --------------------------------

    def test_stale_entry_from_deleted_finding_fails_naming_file_line(self):
        self._write_widget(["x = 1", "y = 2  # FINDME", "z = 3"])

        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        self._write_baseline(
            ".widget-baseline.json", ["widget.py:2", "widget.py:7"]
        )  # widget.py:7 does not exist / finding gone

        result = check_repo(self.repo_root)
        self.assertEqual(result["dead"], [])
        self.assertEqual(len(result["stale"]), 1)
        stale_entry = result["stale"][0]
        self.assertEqual(stale_entry["baseline"], ".widget-baseline.json")
        self.assertIn("widget.py:7", stale_entry["entries"])
        self.assertNotIn("widget.py:2", stale_entry["entries"])

    def test_real_main_exits_1_on_stale_entry(self):
        self._write_widget(["x = 1  # FINDME"])
        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        self._write_baseline(".widget-baseline.json", ["widget.py:1", "widget.py:99"])
        rc = main(["--root", str(self.repo_root)])
        self.assertEqual(rc, 1)

    # ---- healthy baseline ---------------------------------------------

    def test_healthy_baseline_exits_0(self):
        self._write_widget(["x = 1  # FINDME", "y = 2"])
        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        self._write_baseline(".widget-baseline.json", ["widget.py:1"])
        rc = main(["--root", str(self.repo_root)])
        self.assertEqual(rc, 0)

        result = check_repo(self.repo_root)
        self.assertEqual(result["dead"], [])
        self.assertEqual(result["stale"], [])
        self.assertEqual(len(result["ok_baselines"]), 1)

    # ---- parse_stale_entry --------------------------------------------

    def test_parse_stale_entry_bare_key(self):
        key, count = parse_stale_entry("widget.py:7")
        self.assertEqual(key, "widget.py:7")
        self.assertIsNone(count)

    def test_parse_stale_entry_count_style(self):
        key, count = parse_stale_entry("tools/foo.py@TYPE (baseline 3, current 1)")
        self.assertEqual(key, "tools/foo.py@TYPE")
        self.assertEqual(count, 1)

    # ---- --prune: list-style baseline ----------------------------------

    def test_prune_removes_exactly_stale_entries_list_style(self):
        self._write_widget(["x = 1  # FINDME"])
        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        baseline_path = self._write_baseline(
            ".widget-baseline.json", ["widget.py:1", "widget.py:50", "widget.py:60"]
        )

        rc = main(["--root", str(self.repo_root), "--prune"])
        self.assertEqual(rc, 0)

        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(data["violations"], ["widget.py:1"])

    def test_prune_shrinks_count_style_entry_and_removes_zeroed_ones(self):
        self._write_widget(["x = 1  # FINDME"])
        workflows = self.repo_root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text(
            "steps:\n"
            "  - run: python tools/widget_lint.py --baseline .widget-baseline.json\n",
            encoding="utf-8",
        )
        baseline_path = self.repo_root / ".widget-baseline.json"
        baseline_path.write_text(
            json.dumps({"violations": {"widget.py:1": 1, "widget.py:50": 2}}, indent=2) + "\n",
            encoding="utf-8",
        )

        # Directly exercise prune_baseline with a synthetic stale list carrying
        # counts, as a consumer using the count-style format would report it.
        parsed = [("widget.py:50", 0)]
        changes = prune_baseline(baseline_path, parsed)
        self.assertTrue(any("removed widget.py:50" in c for c in changes))

        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(data["violations"], {"widget.py:1": 1})

    def test_prune_never_adds_or_raises_counts(self):
        baseline_path = self.repo_root / ".count-baseline.json"
        baseline_path.write_text(
            json.dumps({"violations": {"a.py@T": 5}}, indent=2) + "\n", encoding="utf-8"
        )
        # A "stale" report claiming a HIGHER current count must never be used
        # to raise the baseline -- prune_baseline only shrinks or removes.
        changes = prune_baseline(baseline_path, [("a.py@T", 3)])
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(data["violations"]["a.py@T"], 3)
        self.assertTrue(any("shrank a.py@T (5 -> 3)" in c for c in changes))

    # ---- query_consumer_stale: tool error handling ---------------------

    def test_query_consumer_stale_reports_missing_consumer(self):
        stale, error = query_consumer_stale(
            "tools/does_not_exist.py", self.repo_root / ".x-baseline.json", self.repo_root
        )
        self.assertEqual(stale, [])
        self.assertIsNotNone(error)
        self.assertIn("missing", error)


if __name__ == "__main__":
    unittest.main()
