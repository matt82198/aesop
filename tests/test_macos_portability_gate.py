"""Tests for tools/macos_portability_gate.py.

S1 TDD specification:
- Catches bare `spawnSync('python', ...)` / `execFileSync('python', ...)` in
  `.mjs` / `.js` files that lack a `python3` fallback/resolver.
- Allows `spawnSync` / `execFileSync` using a `resolvePythonBin()` helper or
  explicit `python3` resolution before `python`.
- Catches unguarded `import winreg` / `ctypes.windll` in `.py` files without a
  `win32` / `nt` / `Windows` guard or `# macos-port-exempt` marker.
- Allows guarded Windows-only blocks (`if sys.platform == "win32":`,
  `if os.name == "nt":`, `if platform.system() == "Windows":`).
- Catches un-baselined hardcoded personal paths (`C:/Users/Matt` + `t`,
  `/c/Users/matt` + `8`, `C--Users-Matt` + `t`) in non-test production files.
- Passes (exit 0) on the real repository tree once S2 fixes are applied.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "tools" / "macos_portability_gate.py"


def _run_gate(root, profile="aesop", extra_args=None):
    if not GATE_PATH.exists():
        raise AssertionError("missing gate script: %s" % GATE_PATH)
    cmd = [
        sys.executable,
        str(GATE_PATH),
        "--root",
        str(root),
        "--profile",
        profile,
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


class TestMacosPortabilityGateJS(unittest.TestCase):
    """JS/MJS bare `python` invocation detection."""

    def test_catches_bare_spawnSync_python_without_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "monitor" / "sample.mjs",
                "import { spawnSync } from 'node:child_process';\n"
                "export function run() {\n"
                "  return spawnSync('python', ['tool.py'], { encoding: 'utf8' });\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertGreaterEqual(len(data["findings"]), 1)
            self.assertEqual(data["findings"][0]["rule"], "js-bare-python")

    def test_catches_bare_execFileSync_python_without_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "helper.js",
                "const { execFileSync } = require('child_process');\n"
                "function rotate() {\n"
                "  execFileSync('python', ['rotate_logs.py']);\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["findings"][0]["rule"], "js-bare-python")

    def test_allows_python3_resolver_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "good.mjs",
                "import { spawnSync } from 'node:child_process';\n"
                "function resolvePythonBin() {\n"
                "  const r = spawnSync('python3', ['--version'], { stdio: 'ignore' });\n"
                "  if (!r.error && r.status === 0) return 'python3';\n"
                "  return 'python';\n"
                "}\n"
                "export function run() {\n"
                "  return spawnSync(resolvePythonBin(), ['tool.py']);\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_allows_python3_first_then_python_fallback_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "bin" / "entry.js",
                "const { spawnSync } = require('child_process');\n"
                "function runInit(script) {\n"
                "  const res = spawnSync('python3', [script]);\n"
                "  if (res.error) {\n"
                "    return spawnSync('python', [script]);\n"
                "  }\n"
                "  return res;\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_catches_indirect_fallback_and_argv_without_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "fallback.mjs",
                "const py = process.env.PY || 'python';\n"
                "export default py;\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["findings"][0]["rule"], "js-bare-python")

        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "argv.js",
                "function call(cmd, argv) { return [cmd, argv]; }\n"
                "function run(script) {\n"
                "  return call('x', ['python', '-I', script]);\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["findings"][0]["rule"], "js-bare-python")

    def test_allows_indirect_fallback_and_argv_with_resolver_or_python3(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "good_indirect.mjs",
                "import { resolvePythonBin } from './py.mjs';\n"
                "const py = process.env.PY || 'python';\n"
                "export function run(script) {\n"
                "  const bin = resolvePythonBin();\n"
                "  return [bin, ['python', '-I', script], py];\n"
                "}\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "good_python3.js",
                "const bins = ['python', 'python3'];\n"
                "const py = process.env.PY || 'python';\n"
                "function call(cmd, argv) { return [cmd, argv]; }\n"
                "call('x', ['python', '-I', 'tool.py']);\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestMacosPortabilityGatePythonWinAPIs(unittest.TestCase):
    """Unguarded Windows-only Python API detection."""

    def test_catches_unguarded_import_winreg(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "bad_reg.py",
                "import winreg\n"
                "def read_val():\n"
                "    return winreg.HKEY_CURRENT_USER\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["findings"][0]["rule"], "py-unguarded-win-api")

    def test_catches_unguarded_ctypes_windll(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "bad_dll.py",
                "import ctypes\n"
                "def get_tick():\n"
                "    return ctypes.windll.kernel32.GetTickCount()\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["findings"][0]["rule"], "py-unguarded-win-api")

    def test_allows_platform_guarded_win_apis_and_exempt_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tools" / "good_win.py",
                "import ctypes\n"
                "import sys\n"
                "if sys.platform == 'win32':\n"
                "    import winreg\n"
                "    _ = ctypes.windll.kernel32\n"
                "def maybe_dll():\n"
                "    if sys.platform == 'win32':\n"
                "        return ctypes.windll.kernel32.GetTickCount()\n"
                "    return 0\n"
                "def explicit_exempt():\n"
                "    return ctypes.windll.kernel32  # macos-port-exempt\n",
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestMacosPortabilityGateHardcodedPaths(unittest.TestCase):
    """Hardcoded personal/Windows path detection."""

    def test_catches_unbaselined_personal_paths_in_production_files(self):
        win_path = "C:/Users/" + "Mattt" + "/aesop"
        gitbash_path = "/c/Users/" + "matt8" + "/scripts"
        claude_proj_slug = "C--Users-" + "Mattt" + "-aesop"
        for idx, sample in enumerate((win_path, gitbash_path, claude_proj_slug)):
            with tempfile.TemporaryDirectory() as tmp:
                _write(
                    Path(tmp) / "tools" / ("leak_%d.py" % idx),
                    'ROOT = "%s"\n' % sample,
                )
                proc = _run_gate(tmp, extra_args=["--json"])
                self.assertEqual(
                    proc.returncode,
                    1,
                    "expected exit 1 for %s, got: %s" % (sample, proc.stdout),
                )
                data = json.loads(proc.stdout)
                self.assertEqual(data["findings"][0]["rule"], "hardcoded-user-path")

    def test_ignores_tests_directory(self):
        win_path = "C:/Users/" + "Mattt" + "/aesop"
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "tests" / "test_fixture.py",
                'FIXTURE = "%s"\n' % win_path,
            )
            proc = _run_gate(tmp, extra_args=["--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestMacosPortabilityGateRealTree(unittest.TestCase):
    """Gate passes on the fixed aesop repository tree."""

    def test_passes_on_aesop_repo_root(self):
        proc = _run_gate(REPO_ROOT, profile="aesop")
        self.assertEqual(
            proc.returncode,
            0,
            "macos_portability_gate failed on %s:\n%s\n%s"
            % (REPO_ROOT, proc.stdout, proc.stderr),
        )


if __name__ == "__main__":
    unittest.main()
