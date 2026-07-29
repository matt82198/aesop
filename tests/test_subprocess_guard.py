"""
Tests for tools/subprocess_guard.py -- G6 guardrail.

AST guard extending the test-hygiene framework (test_test_hygiene.py) with a
dedicated scanner for subprocess brittleness patterns that have bitten
Windows CI:
  - bare `subprocess.run(['bash', ...])` / Popen without an explicit cwd=
  - `shell=True` (shell-injection risk)
  - explicit `cwd=None`
  - `os.system()` calls

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
TOOL = REPO_ROOT / "tools" / "subprocess_guard.py"

sys.path.insert(0, str(REPO_ROOT))
from tools import subprocess_guard  # noqa: E402


class SubprocessGuardFixtureCase(unittest.TestCase):
    """Base: isolated temp dir holding fixture .py files, cleaned up in tearDown."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="aesop-subprocess-guard-")
        self.fixtures_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def write_fixture(self, name, content):
        path = self.fixtures_dir / name
        path.write_text(content, encoding="utf-8")
        return path


class TestBareBashWithoutCwd(SubprocessGuardFixtureCase):
    def test_detects_bare_subprocess_run_bash_without_cwd(self):
        self.write_fixture(
            "test_bad_bash.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['bash', 'script.sh'])\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        rules = [f["rule"] for f in findings]
        self.assertIn("BARE_BASH_NO_CWD", rules)

    def test_detects_bare_subprocess_popen_bash_without_cwd(self):
        self.write_fixture(
            "test_bad_bash_popen.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.Popen(['bash', '-c', 'echo hi'])\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        rules = [f["rule"] for f in findings]
        self.assertIn("BARE_BASH_NO_CWD", rules)


class TestShellTrue(SubprocessGuardFixtureCase):
    def test_detects_shell_true(self):
        self.write_fixture(
            "test_bad_shell.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run('echo hi', shell=True, cwd='/tmp')\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        rules = [f["rule"] for f in findings]
        self.assertIn("SHELL_TRUE", rules)


class TestOsSystem(SubprocessGuardFixtureCase):
    def test_detects_os_system_call(self):
        self.write_fixture(
            "test_bad_os_system.py",
            "import os\n"
            "def test_thing():\n"
            "    os.system('echo hi')\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        rules = [f["rule"] for f in findings]
        self.assertIn("OS_SYSTEM", rules)


class TestExplicitCwdNone(SubprocessGuardFixtureCase):
    def test_detects_explicit_cwd_none(self):
        self.write_fixture(
            "test_bad_cwd_none.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['ls'], cwd=None)\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        rules = [f["rule"] for f in findings]
        self.assertIn("CWD_NONE", rules)


class TestExplicitCwdPasses(SubprocessGuardFixtureCase):
    def test_subprocess_with_explicit_cwd_path_is_clean(self):
        self.write_fixture(
            "test_good_cwd.py",
            "import subprocess\n"
            "import tempfile\n"
            "def test_thing():\n"
            "    with tempfile.TemporaryDirectory() as d:\n"
            "        subprocess.run(['bash', 'script.sh'], cwd=d)\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        self.assertEqual(findings, [])

    def test_normal_subprocess_call_without_bash_or_shell_is_clean(self):
        self.write_fixture(
            "test_good_plain.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['git', 'status'], cwd='/repo', capture_output=True)\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        self.assertEqual(findings, [])


class TestSuppressionMarker(SubprocessGuardFixtureCase):
    def test_subprocess_ok_comment_suppresses_finding(self):
        self.write_fixture(
            "test_suppressed.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['bash', 'script.sh'])  # subprocess-ok\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        self.assertEqual(findings, [])

    def test_suppression_is_scoped_to_the_marked_call_only(self):
        self.write_fixture(
            "test_partial_suppressed.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['bash', 'ok.sh'])  # subprocess-ok\n"
            "    subprocess.run(['bash', 'bad.sh'])\n",
        )
        findings = subprocess_guard.scan_paths([str(self.fixtures_dir)], repo_root=self.fixtures_dir)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 4)


class TestJsonOutput(SubprocessGuardFixtureCase):
    def test_json_output_format_via_cli(self):
        self.write_fixture(
            "test_bad_for_json.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['bash', 'script.sh'])\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--json", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
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
        self.assertEqual(finding["rule"], "BARE_BASH_NO_CWD")

    def test_json_output_clean_when_no_findings(self):
        self.write_fixture(
            "test_clean_for_json.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['git', 'status'], cwd='/repo')\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--json", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])


class TestCliExitCodes(SubprocessGuardFixtureCase):
    def test_check_mode_exit_1_on_findings(self):
        self.write_fixture(
            "test_bad_exit.py",
            "import os\n"
            "def test_thing():\n"
            "    os.system('echo hi')\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check", "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("OS_SYSTEM", result.stdout)

    def test_exit_0_on_clean_tree(self):
        self.write_fixture(
            "test_clean_exit.py",
            "import subprocess\n"
            "def test_thing():\n"
            "    subprocess.run(['echo', 'hi'], cwd='/tmp')\n",
        )
        result = subprocess.run(
            [sys.executable, str(TOOL), "--paths", str(self.fixtures_dir)],
            capture_output=True,
            text=True,
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("PASS", result.stdout)

    def test_help_exits_zero_with_usage_on_stdout(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            capture_output=True,
            text=True,
            cwd=str(self.fixtures_dir),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
