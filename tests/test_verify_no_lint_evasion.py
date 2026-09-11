#!/usr/bin/env python3
"""Tests for tools/verify_no_lint_evasion.py (Guardrail G11: lint-evasion detector).

Reproduces the real escape: an agent split a gate-matched control filename into
adjacent string-literal fragments so the owning gate's literal scan stopped
matching, while the reconstructed value stayed identical.

Every fixture repo is built in a fresh temp dir; the detector is invoked as a
subprocess through sys.executable with an explicit timeout and utf-8 encoding.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DETECTOR = REPO_ROOT / "tools" / "verify_no_lint_evasion.py"

TIMEOUT = 180

# A minimal stand-in for the real gate module: the detector derives its token
# list by AST-parsing the *_TO_PROTECT assignments out of this file.
STUB_GATE_MODULE = '''"""Stub gate module for fixture repos."""
STATE_FILES_TO_PROTECT = [
    "tracker.json",
    "orchestrator-status.json",
    "OUTCOMES-LEDGER.md",
    ".watchdog-heartbeat",
]

MARKDOWN_FILES_TO_PROTECT = [
    "STATE.md",
    "BUILDLOG.md",
]
'''


def run_detector(*args):
    """Run the detector as a subprocess; return CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(DETECTOR)] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=TIMEOUT,
        cwd=str(REPO_ROOT),
    )


class FixtureRepo:
    """Temp repo with a stub gate module; context manager yielding its root."""

    def __init__(self, with_gate_module=True):
        self._tmp = None
        self._with_gate_module = with_gate_module
        self.root = None

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lint-evasion-")
        self.root = Path(self._tmp.name)
        tools_dir = self.root / "tools"
        tools_dir.mkdir(parents=True)
        if self._with_gate_module:
            (tools_dir / "stateapi_lint.py").write_text(
                STUB_GATE_MODULE, encoding="utf-8"
            )
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False

    def write(self, rel, content):
        """Write a source file into the fixture repo."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def scan(self, *extra):
        """Run the detector against this fixture repo."""
        return run_detector("--root", str(self.root), *extra)


class TestIncidentReproduction(unittest.TestCase):
    """Evidence 1: the actual incident commit's pattern must be flagged."""

    INCIDENT_SOURCE = """import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_dir = Path(tmpdir) / 'state' / 'ledger'
        ledger_dir.mkdir(parents=True)
        ledger_file = ledger_dir / ('OUTCOMES' + '-LEDGER' + '.md')
        ledger_file.write_text('fixture', encoding='utf-8')
"""

    def test_incident_pattern_is_flagged(self):
        with FixtureRepo() as repo:
            repo.write("tools/verify_cost_summary_drawer.py", self.INCIDENT_SOURCE)
            result = repo.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("verify_cost_summary_drawer.py", result.stdout)
        self.assertIn("OUTCOMES-LEDGER.md", result.stdout)

    def test_incident_finding_names_file_line_and_token(self):
        import json

        with FixtureRepo() as repo:
            repo.write("tools/verify_cost_summary_drawer.py", self.INCIDENT_SOURCE)
            result = repo.scan("--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["findings"]), 1)
        finding = payload["findings"][0]
        self.assertEqual(finding["file"], "tools/verify_cost_summary_drawer.py")
        self.assertEqual(finding["line"], 9)
        self.assertEqual(finding["token"], "OUTCOMES-LEDGER.md")
        self.assertEqual(finding["value"], "OUTCOMES-LEDGER.md")
        self.assertEqual(
            finding["fragments"], ["OUTCOMES", "-LEDGER", ".md"]
        )


class TestOtherEvasionShapes(unittest.TestCase):
    """Split-token constructions in every supported shape must be flagged."""

    def _assert_flagged(self, rel, source):
        with FixtureRepo() as repo:
            repo.write(rel, source)
            result = repo.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return result.stdout

    def test_two_fragment_split(self):
        self._assert_flagged(
            "tools/a.py", "name = 'tracker' + '.json'\n"
        )

    def test_split_after_variable_prefix(self):
        # Left-associative chain: the constant run is not a subtree, so the
        # scanner must still find the adjacent-constant run.
        self._assert_flagged(
            "tools/b.py", "import os\nname = os.sep + 'tracker' + '.json'\n"
        )

    def test_str_join_of_fragments(self):
        self._assert_flagged(
            "tools/c.py", "name = ''.join(['orchestrator-status', '.json'])\n"
        )

    def test_fstring_of_constant_fragments(self):
        self._assert_flagged(
            "tools/d.py", "name = f\"{'BUILD'}{'LOG.md'}\"\n"
        )

    def test_markdown_control_file_split(self):
        self._assert_flagged(
            "tools/e.py", "name = 'STATE' + '.md'\n"
        )

    def test_heartbeat_split(self):
        self._assert_flagged(
            "tools/f.py", "name = '.watchdog' + '-heartbeat'\n"
        )

    def test_builtin_baseline_token_split(self):
        self._assert_flagged(
            "tools/g.py", "name = '.stateapi' + '-baseline.json'\n"
        )

    def test_javascript_concat_split(self):
        out = self._assert_flagged(
            "ui/x.mjs", "const f = 'tracker' + '.json';\n"
        )
        self.assertIn("ui/x.mjs", out)

    def test_javascript_template_literal_split(self):
        self._assert_flagged(
            "ui/y.js", "const f = `orchestrator-status` + `.json`;\n"
        )


class TestNoFalsePositives(unittest.TestCase):
    """Benign and sanctioned constructions must not be flagged."""

    def _assert_clean(self, rel, source):
        with FixtureRepo() as repo:
            repo.write(rel, source)
            result = repo.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_plain_literal_is_not_evasion(self):
        # The owning gate can still see this; it is that gate's job, not ours.
        self._assert_clean("tools/a.py", "name = 'tracker.json'\n")

    def test_whole_token_in_one_fragment_is_not_evasion(self):
        self._assert_clean(
            "tools/b.py", "import os\nname = 'state' + os.sep + 'tracker.json'\n"
        )

    def test_unrelated_concat_is_not_flagged(self):
        self._assert_clean(
            "tools/c.py", "msg = 'hello ' + 'world' + '!'\n"
        )

    def test_similar_but_distinct_filename_is_not_flagged(self):
        # Word-boundary anchoring: my-tracker.json is not tracker.json.
        self._assert_clean(
            "tools/d.py", "name = 'my-tracker' + '.json'\n"
        )

    def test_token_embedded_in_longer_identifier_is_not_flagged(self):
        self._assert_clean(
            "tools/e.py", "name = 'STATE' + '.mdx'\n"
        )

    def test_suppression_marker_silences_finding(self):
        self._assert_clean(
            "tools/f.py", "name = 'tracker' + '.json'  # lint-evasion-ok\n"
        )

    def test_js_suppression_marker_silences_finding(self):
        self._assert_clean(
            "ui/g.mjs", "const f = 'tracker' + '.json'; // lint-evasion-ok\n"
        )

    def test_non_source_files_ignored(self):
        self._assert_clean(
            "docs/h.md", "name = 'tracker' + '.json'\n"
        )

    def test_syntactically_broken_python_is_skipped(self):
        self._assert_clean("tools/i.py", "def broken(:\n")

    def test_missing_gate_module_still_runs_on_builtins(self):
        with FixtureRepo(with_gate_module=False) as repo:
            repo.write("tools/a.py", "name = 'tracker' + '.json'\n")
            result = repo.scan()
        # No gate module -> tracker.json is not a derived token, so clean,
        # and the note records the degraded token source.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("builtin-only", result.stdout)


class TestSanctionedSecretFixtures(unittest.TestCase):
    """Evidence 3: the sanctioned dummy-secret concat pattern must stay clean."""

    def test_runtime_assembled_dummy_secrets_are_exempt(self):
        # Mirrors tools/scanner_selftest.py: dummy credentials MUST be split so
        # the pattern text never appears contiguously in the repo. This fixture
        # source is itself assembled at runtime for the same reason -- a literal
        # here would trip the push-time secret gate.
        assign = "to" + "ken = "
        source = (
            "def fixtures():\n"
            "    a = f\"" + assign + "'{'gh' + 'p_' + 'a' * 32}'\"\n"
            "    b = 'AK' + 'IA' + 'A' * 16\n"
            "    c = '-----BEGIN RSA ' + 'PRIVATE' + ' KEY-----'\n"
            "    d = 'sk' + '-' + 'x' * 40\n"
            "    return a, b, c, d\n"
        )
        with FixtureRepo() as repo:
            repo.write("tools/scanner_selftest.py", source)
            result = repo.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_tests_fixtures_tree_is_exempt(self):
        with FixtureRepo() as repo:
            repo.write(
                "tests/fixtures/sample.py", "name = 'tracker' + '.json'\n"
            )
            repo.write(
                "tests/deep/fixtures/sample.py", "name = 'STATE' + '.md'\n"
            )
            result = repo.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_test_fixtures_dir_is_not_exempt(self):
        # Only tests/.../fixtures is sanctioned; a top-level fixtures/ is not.
        with FixtureRepo() as repo:
            repo.write("fixtures/sample.py", "name = 'tracker' + '.json'\n")
            result = repo.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


class TestCli(unittest.TestCase):
    """CLI contract: exit codes, flags, fail-closed behaviour."""

    def test_help_exits_zero(self):
        result = run_detector("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("lint-evasion", result.stdout)

    def test_unknown_flag_exits_two(self):
        result = run_detector("--bogus")
        self.assertEqual(result.returncode, 2)

    def test_root_without_value_exits_two(self):
        result = run_detector("--root")
        self.assertEqual(result.returncode, 2)

    def test_check_flag_accepted(self):
        with FixtureRepo() as repo:
            repo.write("tools/a.py", "name = 'ok'\n")
            result = repo.scan("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_paths_flag_narrows_scan(self):
        with FixtureRepo() as repo:
            repo.write("tools/bad.py", "name = 'tracker' + '.json'\n")
            repo.write("ui/good.py", "name = 'fine'\n")
            narrow = repo.scan("--paths", str(repo.root / "ui"))
            wide = repo.scan()
        self.assertEqual(narrow.returncode, 0, narrow.stdout + narrow.stderr)
        self.assertEqual(wide.returncode, 1, wide.stdout + wide.stderr)

    def test_json_output_is_parseable_when_clean(self):
        import json

        with FixtureRepo() as repo:
            repo.write("tools/a.py", "name = 'ok'\n")
            result = repo.scan("--json")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])


class TestRealTree(unittest.TestCase):
    """Evidence 2: scan the real repository tree.

    The first run of this detector against origin/main found a SECOND live
    escape, independent of the incident that prompted the guardrail: commit
    16b3f8e3 (2026-07-31) split two heartbeat filenames the same way, and the
    stateapi ratchet baseline was then lowered 39 -> 37 on the strength of it.
    Remediating that needs facade routing plus a baseline change, both outside
    this guardrail's ownership, so the escape is recorded here EXPLICITLY rather
    than exempted: the detector still exits 1 on the real tree (it reports the
    truth), and this ratchet fails if the finding set moves in either direction
    -- a new evasion appears, or these are fixed and the entry is now stale.

    The detector must not be wired into CI until this set is empty.
    """

    KNOWN_ESCAPES = {
        ("tools/health_checks.py", ".watchdog-heartbeat"),
        ("tools/health_checks.py", ".monitor-heartbeat"),
    }

    def test_repo_tree_matches_known_escape_ratchet(self):
        import json

        result = run_detector("--root", str(REPO_ROOT), "--json")
        self.assertIn(
            result.returncode, (0, 1),
            "detector errored on the real tree:\n" + result.stderr
        )
        payload = json.loads(result.stdout)
        found = {(f["file"], f["token"]) for f in payload["findings"]}

        new = found - self.KNOWN_ESCAPES
        self.assertEqual(
            new, set(),
            "NEW lint evasion on the real tree (fix it, do not add it here): %r"
            % sorted(new)
        )

        stale = self.KNOWN_ESCAPES - found
        self.assertEqual(
            stale, set(),
            "Known escapes are fixed -- remove them from KNOWN_ESCAPES: %r"
            % sorted(stale)
        )

    def test_detector_derives_tokens_from_the_real_gate_module(self):
        import json

        result = run_detector("--root", str(REPO_ROOT), "--json")
        payload = json.loads(result.stdout)
        self.assertIn("derived from gate module", payload["token_source"])
        self.assertNotIn("builtin-only", payload["token_source"])


class TestHygiene(unittest.TestCase):
    """Tests must not pollute cwd or global state."""

    def test_cwd_unchanged_after_scan(self):
        before = os.getcwd()
        with FixtureRepo() as repo:
            repo.write("tools/a.py", "name = 'tracker' + '.json'\n")
            repo.scan()
        self.assertEqual(os.getcwd(), before)


if __name__ == "__main__":
    unittest.main()
