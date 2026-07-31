"""
Tests for tools/test_discovery_check.py -- discovery-invisibility guard.

AST guard detecting two shapes that `unittest discover` (this repo's actual
test runner; `npm run test:py`) silently collects ZERO tests from:
  - a module-level `def test_*():` outside any class (BARE_TEST_FUNCTION)
  - a `class Test*:` with test_* methods but no base class at all
    (BASELESS_TEST_CLASS) -- plain classes with zero inheritance that
    unittest discover ignores

Root cause: this exact class of defect (a PR's new tests/test_*.py file
carrying baseless Test* classes) reached CI 10 times in a row on
guard/toolchain-health because tests/test_no_bare_test_functions.py already
catches it, but only as part of the full `npm run test:py` suite -- which
hooks/pre-push-policy.sh does not run pre-push (by design; it's slow). This
tool is the fast, standalone, near-instant equivalent so it CAN be run (or
wired) as a targeted local/CI gate without paying for the full suite.

All fixtures are written to an isolated tempfile.TemporaryDirectory() so
nothing here touches the real tests/ tree or pollutes cwd; setUp/tearDown
handle isolation per the tests/CLAUDE.md hygiene rules.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "test_discovery_check.py"

sys.path.insert(0, str(REPO_ROOT))
from tools import test_discovery_check  # noqa: E402


class DiscoveryCheckFixtureCase(unittest.TestCase):
    """Base: isolated temp dir holding fixture .py files, cleaned up in tearDown."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="aesop-test-discovery-check-")
        self.fixtures_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def write_fixture(self, name, content):
        path = self.fixtures_dir / name
        path.write_text(content, encoding="utf-8")
        return path


class TestBareTestFunctionDetection(DiscoveryCheckFixtureCase):
    def test_detects_module_level_test_function(self):
        self.write_fixture(
            "test_bare_fn.py",
            "def test_something():\n"
            "    assert True\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(scanned, 1)
        rules = [f["rule"] for f in findings]
        self.assertIn("BARE_TEST_FUNCTION", rules)

    def test_method_inside_a_class_is_not_flagged(self):
        self.write_fixture(
            "test_ok_method.py",
            "import unittest\n"
            "class TestThing(unittest.TestCase):\n"
            "    def test_something(self):\n"
            "        self.assertTrue(True)\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])


class TestBaselessTestClassDetection(DiscoveryCheckFixtureCase):
    def test_detects_baseless_class_with_test_methods(self):
        # This is exactly the shape that broke guard/toolchain-health's CI
        # runs: classes with no base at all.
        self.write_fixture(
            "test_toolchain_health.py",
            "class TestBinaryChecks:\n"
            "    def test_binary_present(self):\n"
            "        assert True\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        rules = [f["rule"] for f in findings]
        self.assertIn("BASELESS_TEST_CLASS", rules)
        finding = next(f for f in findings if f["rule"] == "BASELESS_TEST_CLASS")
        self.assertEqual(finding["line"], 1)

    def test_test_class_subclassing_testcase_is_clean(self):
        self.write_fixture(
            "test_ok_class.py",
            "import unittest\n"
            "class TestThing(unittest.TestCase):\n"
            "    def test_something(self):\n"
            "        self.assertTrue(True)\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])

    def test_test_class_subclassing_any_explicit_base_is_clean(self):
        # Not just unittest.TestCase directly -- any explicit base (e.g. a
        # shared fixture mixin) still makes it visible via MRO in practice
        # in this repo's suites, so only bases=[] (nothing at all) fires.
        self.write_fixture(
            "test_ok_mixin.py",
            "class _Base:\n"
            "    pass\n"
            "class TestThing(_Base):\n"
            "    def test_something(self):\n"
            "        pass\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])

    def test_baseless_class_without_test_methods_is_not_flagged(self):
        # e.g. a plain helper class named TestFixtures with no test_* methods
        # is not a test-discovery hazard.
        self.write_fixture(
            "test_helper_class.py",
            "class TestFixtures:\n"
            "    def build(self):\n"
            "        return object()\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])


class TestSuppression(DiscoveryCheckFixtureCase):
    def test_discovery_ok_comment_suppresses_bare_function(self):
        self.write_fixture(
            "test_suppressed_fn.py",
            "def test_something():  # discovery-ok\n"
            "    assert True\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])

    def test_discovery_ok_comment_suppresses_baseless_class(self):
        self.write_fixture(
            "test_suppressed_class.py",
            "class TestThing:  # discovery-ok\n"
            "    def test_something(self):\n"
            "        pass\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(findings, [])

    def test_suppression_does_not_leak_to_a_second_unmarked_finding(self):
        self.write_fixture(
            "test_partial_suppress.py",
            "class TestOk:  # discovery-ok\n"
            "    def test_something(self):\n"
            "        pass\n"
            "class TestBad:\n"
            "    def test_other(self):\n"
            "        pass\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["file"], "test_partial_suppress.py")


class TestSelfExclusion(DiscoveryCheckFixtureCase):
    def test_the_regression_test_file_itself_is_skipped(self):
        # tests/test_no_bare_test_functions.py legitimately defines
        # `bare_functions = []` etc. as data, not as a violation of itself;
        # mirror its own self-skip convention.
        self.write_fixture(
            "test_no_bare_test_functions.py",
            "def test_should_be_ignored():\n"
            "    assert True\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        self.assertEqual(scanned, 0)
        self.assertEqual(findings, [])


class TestParseError(DiscoveryCheckFixtureCase):
    def test_syntax_error_is_reported_not_silently_skipped(self):
        self.write_fixture(
            "test_broken_syntax.py",
            "def test_thing(:\n"
            "    pass\n",
        )
        findings, scanned, missing = test_discovery_check.scan_paths(
            [str(self.fixtures_dir)], repo_root=self.fixtures_dir
        )
        self.assertEqual(missing, [])
        rules = [f["rule"] for f in findings]
        self.assertIn("PARSE_ERROR", rules)


class TestCLIJsonOutput(DiscoveryCheckFixtureCase):
    def test_json_output_format_via_cli(self):
        self.write_fixture(
            "test_bad_for_json.py",
            "class TestBad:\n"
            "    def test_thing(self):\n"
            "        pass\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--json", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("ok", payload)
        self.assertIn("findings", payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["findings"]), 1)
        finding = payload["findings"][0]
        self.assertIn("file", finding)
        self.assertIn("line", finding)
        self.assertIn("rule", finding)
        self.assertIn("message", finding)
        self.assertEqual(finding["rule"], "BASELESS_TEST_CLASS")

    def test_json_output_clean_when_no_findings(self):
        self.write_fixture(
            "test_clean_for_json.py",
            "import unittest\n"
            "class TestThing(unittest.TestCase):\n"
            "    def test_something(self):\n"
            "        self.assertTrue(True)\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--json", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])


class TestCLIExitCodeContract(DiscoveryCheckFixtureCase):
    def test_check_mode_exit_1_on_findings(self):
        self.write_fixture(
            "test_bad.py",
            "def test_bare():\n"
            "    pass\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 1)

    def test_exit_0_on_clean_tree(self):
        self.write_fixture(
            "test_clean.py",
            "import unittest\n"
            "class TestOk(unittest.TestCase):\n"
            "    def test_fine(self):\n"
            "        self.assertTrue(True)\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exit_2_on_nonexistent_path(self):
        nonexistent = self.fixtures_dir / "does-not-exist"
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check", "--paths", str(nonexistent)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 2)

    def test_exit_2_on_empty_directory_never_collapses_to_0(self):
        # An empty scan target must never report a false-clean 0: the repo's
        # most recurrent defect class is a gate that "passed" because it
        # scanned nothing.
        empty_dir = self.fixtures_dir / "empty"
        empty_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check", "--paths", str(empty_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 2)

    def test_help_exits_zero_with_usage_on_stdout(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
