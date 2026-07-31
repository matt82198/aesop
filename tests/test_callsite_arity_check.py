#!/usr/bin/env python3
"""
Test suite for tools/callsite_arity_check.py.

Root cause under test: a shared helper module's function signature changes
(new required params added) and a caller elsewhere in the repo is not
updated -- surfaces only as a runtime TypeError (this is the exact shape of
the real defect that broke the browser-proofs CI job in PR #652, where
`playwright_common.start_server()` grew two new required params and
`verify_wave_telemetry.run_work_proof()` kept calling the old 2-arg form).

Tests:
1. Reproduction: the exact PR #652 shape (missing required positional args
   after a callee signature grows) is caught.
2. Clean state: matching call sites pass.
3. Zero-input (no .py files to scan) exits 2 -- never collapsed into 0.
4. Non-git, non-existent root exits 2.
5. Edge cases that must NOT false-positive: *args/**kwargs call-site
   unpacking, decorated callees, locally-shadowed imports, keyword-argument
   coverage of required params, defaulted params.
6. Additional defect shapes: too-many-positional-arguments, unexpected
   keyword argument.
7. JSON output shape.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK_SCRIPT = Path(__file__).parent.parent / "tools" / "callsite_arity_check.py"


class TestCallsiteArityCheck(unittest.TestCase):
    """Test suite for the cross-module call-site arity checker."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_root = Path(self.tmpdir)

        subprocess.run(["git", "init"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                        cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                        cwd=self.repo_root, capture_output=True, check=True)
        (self.repo_root / "tools").mkdir()

        subprocess.run(["git", "add", "-A"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial", "--allow-empty"],
                        cwd=self.repo_root, capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, relpath, content):
        p = self.repo_root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _stage_all(self):
        subprocess.run(["git", "add", "-A"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "wip", "--allow-empty"],
                        cwd=self.repo_root, capture_output=True, check=True)

    def _run(self, extra_args=None):
        args = [sys.executable, str(CHECK_SCRIPT), "--json", "--root", str(self.repo_root)]
        if extra_args:
            args.extend(extra_args)
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=30)
        return result

    # ---- 1. Reproduction: real PR #652 shape ----------------------------

    def test_catches_missing_required_arg_after_signature_growth(self):
        self._write("tools/playwright_common.py", (
            "from pathlib import Path\n\n\n"
            "def start_server(root, port, repo, serve_script,\n"
            "                 boot_tries=50, boot_sleep=0.2,\n"
            "                 collect_interval='0.3'):\n"
            "    pass\n\n\n"
            "def free_port():\n"
            "    return 1234\n"
        ))
        self._write("tools/verify_wave_telemetry.py", (
            "from pathlib import Path\n"
            "from playwright_common import start_server, free_port\n\n\n"
            "def run_work_proof():\n"
            "    port = free_port()\n"
            "    root = Path('.')\n"
            "    server = start_server(root, port)\n"
            "    return server\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "findings")
        self.assertEqual(len(data["findings"]), 1)
        finding = data["findings"][0]
        self.assertIn("verify_wave_telemetry.py", finding["file"])
        self.assertIn("repo", finding["message"])
        self.assertIn("serve_script", finding["message"])

    # ---- 2. Clean state ---------------------------------------------------

    def test_clean_call_site_passes(self):
        self._write("tools/playwright_common.py", (
            "def start_server(root, port, repo, serve_script,\n"
            "                 boot_tries=50):\n"
            "    pass\n"
        ))
        self._write("tools/verify_dash.py", (
            "from playwright_common import start_server\n\n\n"
            "def run():\n"
            "    return start_server('r', 1, 'repo', 'script')\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "clean")
        self.assertEqual(data["findings"], [])

    def test_keyword_arguments_satisfy_required_params(self):
        self._write("tools/helper.py", (
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1, repo='x', serve_script='y')\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_defaulted_params_not_flagged_as_missing(self):
        self._write("tools/helper.py", (
            "def build(root, port, boot_tries=50, boot_sleep=0.2):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- 3 & 4. Zero-input / unusable root exits 2 -------------------------

    def test_zero_python_files_exits_2(self):
        empty_root = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["git", "init"], cwd=empty_root, capture_output=True, check=True)
            result = subprocess.run(
                [sys.executable, str(CHECK_SCRIPT), "--json", "--root", str(empty_root)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn('"status": "clean"', result.stdout)
        finally:
            shutil.rmtree(empty_root, ignore_errors=True)

    def test_nonexistent_root_exits_2(self):
        result = subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), "--json", "--root", str(self.repo_root / "does-not-exist")],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_non_git_directory_exits_2(self):
        non_git = Path(tempfile.mkdtemp())
        try:
            (non_git / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CHECK_SCRIPT), "--json", "--root", str(non_git)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        finally:
            shutil.rmtree(non_git, ignore_errors=True)

    # ---- 5. False-positive avoidance --------------------------------------

    def test_star_args_unpacking_not_flagged(self):
        self._write("tools/helper.py", (
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run(args):\n"
            "    return build(*args)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_double_star_kwargs_unpacking_not_flagged(self):
        self._write("tools/helper.py", (
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run(kwargs):\n"
            "    return build(**kwargs)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_decorated_callee_skipped(self):
        self._write("tools/helper.py", (
            "import functools\n\n\n"
            "@functools.lru_cache\n"
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_locally_shadowed_import_skipped(self):
        # helper.build() takes 4 required args; caller.py imports it but then
        # defines its OWN 2-arg build() at module level, which shadows the
        # import at runtime (Python's last-binding-wins semantics). The gate
        # must follow the same rule or it would false-positive on this
        # exact PR #652 post-fix shape (verify_wave_telemetry.py etc. kept
        # local start_server() overrides after the shared one grew params).
        self._write("tools/helper.py", (
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def build(root, port):\n"
            "    pass\n\n\n"
            "def run():\n"
            "    return build('r', 1)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_attribute_call_on_module_alias(self):
        self._write("tools/helper.py", (
            "def build(root, port, repo, serve_script):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "import helper\n\n\n"
            "def run():\n"
            "    return helper.build('r', 1)\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["findings"]), 1)

    # ---- 6. Additional defect shapes --------------------------------------

    def test_too_many_positional_arguments_flagged(self):
        self._write("tools/helper.py", (
            "def build(root, port):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1, 'extra')\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("positional", data["findings"][0]["message"])

    def test_unexpected_keyword_argument_flagged(self):
        self._write("tools/helper.py", (
            "def build(root, port):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1, servr='typo')\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("servr", data["findings"][0]["message"])

    def test_varargs_and_varkw_callee_never_flagged(self):
        self._write("tools/helper.py", (
            "def build(root, port, *args, **kwargs):\n"
            "    pass\n"
        ))
        self._write("tools/caller.py", (
            "from helper import build\n\n\n"
            "def run():\n"
            "    return build('r', 1, 2, 3, extra='x')\n"
        ))
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- 7. JSON output shape ----------------------------------------------

    def test_json_output_has_required_keys(self):
        self._write("tools/a.py", "def f():\n    pass\n")
        self._stage_all()

        result = self._run()
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        for key in ("status", "exit_code", "files_scanned", "unparseable_files", "findings"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
