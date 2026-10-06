#!/usr/bin/env python3
"""Tests for templates/ci/* and `aesop init --ci-mode`.

Every template must parse, and every RENDERED workflow must pass the repo's own
CI linters (tools/ci_workflow_lint.py, tools/ci_needs_skip_guard.py) when run
against the scaffolded target -- the real gates, not a re-derivation. Commands
claimed in docs/CI-MODES.md must exist in `node bin/cli.js --help`
(examples invariant: no invented CLI flags).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs pyyaml; locally it is required too
    yaml = None

from tools import init_project  # noqa: E402

TEMPLATES = REPO_ROOT / "templates" / "ci"


def _git_init(td):
    subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, encoding="utf-8", errors="replace", timeout=20)


def _run_tool(tool, td, extra=()):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / tool), "--root", td, *extra],
        cwd=str(REPO_ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=60)


@unittest.skipIf(yaml is None, "PyYAML required (pip install pyyaml)")
class TestTemplatesParse(unittest.TestCase):
    """Raw templates are valid YAML workflows."""

    def test_all_three_templates_exist_and_parse(self):
        for name in ("ci-hosted.yml", "ci-self-hosted-windows.yml", "verify-receipt.yml"):
            path = TEMPLATES / name
            self.assertTrue(path.is_file(), name)
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIsInstance(doc, dict, name)
            self.assertIn("jobs", doc, name)

    def test_placeholder_marker_only_on_pending_template(self):
        self.assertTrue(init_project.is_placeholder_template((TEMPLATES / "verify-receipt.yml").read_text(encoding="utf-8")))
        for name in ("ci-hosted.yml", "ci-self-hosted-windows.yml"):
            self.assertFalse(init_project.is_placeholder_template((TEMPLATES / name).read_text(encoding="utf-8")), name)

    def test_self_hosted_template_routes_forks_to_hosted(self):
        text = (TEMPLATES / "ci-self-hosted-windows.yml").read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        runs_on = [job.get("runs-on") for job in doc["jobs"].values()]
        expr = [r for r in runs_on if isinstance(r, str) and "fork" in r]
        self.assertTrue(expr, runs_on)
        self.assertIn("windows-latest", expr[0])
        self.assertIn(init_project.LABELS_MARKER, expr[0])


@unittest.skipIf(yaml is None, "PyYAML required (pip install pyyaml)")
class TestScaffoldModes(unittest.TestCase):
    """`aesop init --ci-mode X` writes the matching workflow(s) and config block."""

    def _scaffold(self, td, mode, **kw):
        return init_project.init_project(td, project_name="ci-modes", ci_mode=mode, **kw)

    def _assert_linters_pass(self, td):
        lint = _run_tool("ci_workflow_lint.py", td)
        self.assertEqual(lint.returncode, 0, "ci_workflow_lint: " + lint.stdout + lint.stderr)
        guard = _run_tool("ci_needs_skip_guard.py", td)
        self.assertEqual(guard.returncode, 0, "ci_needs_skip_guard: " + guard.stdout + guard.stderr)

    def test_hosted_default(self):
        with tempfile.TemporaryDirectory() as td:
            _git_init(td)
            result = init_project.init_project(td, project_name="ci-modes")
            self.assertEqual(result["ci_mode"], "hosted")
            ci = (Path(td) / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
            self.assertEqual(ci, (TEMPLATES / "ci-hosted.yml").read_text(encoding="utf-8"))
            cfg = json.loads((Path(td) / "aesop.config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["ci"]["mode"], ["hosted"])
            self.assertEqual(cfg["ci"]["windowsMatrix"], "hosted")
            self.assertEqual(cfg["ci"]["receiptGate"], "off")
            self.assertFalse((Path(td) / ".github" / "workflows" / "verify-receipt.yml").exists())
            self._assert_linters_pass(td)

    def test_self_hosted_runner_renders_labels(self):
        with tempfile.TemporaryDirectory() as td:
            _git_init(td)
            result = self._scaffold(td, "self-hosted-runner", self_hosted_labels=["self-hosted", "windows", "lab-7"])
            self.assertIn(".github/workflows/ci.yml", result["files_created"])
            ci_text = (Path(td) / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
            self.assertNotIn(init_project.LABELS_MARKER, ci_text)
            doc = yaml.safe_load(ci_text)
            runs_on = " ".join(str(j.get("runs-on")) for j in doc["jobs"].values())
            self.assertIn('["self-hosted","windows","lab-7"]', runs_on)
            cfg = json.loads((Path(td) / "aesop.config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["ci"]["mode"], ["self-hosted-runner"])
            self.assertEqual(cfg["ci"]["windowsMatrix"], "self-hosted")
            self.assertEqual(cfg["ci"]["selfHostedLabels"], ["self-hosted", "windows", "lab-7"])
            self._assert_linters_pass(td)

    def test_receipt_gate_refuses_placeholder_with_clear_message(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as fake_aesop:
            _git_init(td)
            # fake aesop checkout WITHOUT the receipt lane's workflow -> placeholder path
            (Path(fake_aesop) / ".github" / "workflows").mkdir(parents=True)
            with self.assertRaises(init_project.ScaffoldRefused) as ctx:
                self._scaffold(td, "local-receipt-gate", aesop_root=fake_aesop)
            msg = str(ctx.exception)
            self.assertIn("verify-receipt.yml", msg)
            self.assertIn("feat/receipt-gate-increment-1", msg)
            self.assertFalse((Path(td) / ".github" / "workflows" / "verify-receipt.yml").exists())

    def test_receipt_gate_copies_real_workflow_when_present(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as fake_aesop:
            _git_init(td)
            src = Path(fake_aesop) / ".github" / "workflows" / "verify-receipt.yml"
            src.parent.mkdir(parents=True)
            src.write_text(
                "name: verify-receipt\n"
                "on:\n  pull_request:\n    branches: [main]\n"
                "jobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - run: python tools/verify_receipt.py --check\n",
                encoding="utf-8")
            result = self._scaffold(td, "local-receipt-gate", aesop_root=fake_aesop)
            self.assertIn(".github/workflows/verify-receipt.yml", result["files_created"])
            dest = Path(td) / ".github" / "workflows" / "verify-receipt.yml"
            self.assertEqual(dest.read_text(encoding="utf-8"), src.read_text(encoding="utf-8"))
            cfg = json.loads((Path(td) / "aesop.config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["ci"]["mode"], ["hosted", "local-receipt-gate"])
            self.assertEqual(cfg["ci"]["receiptGate"], "alongside")
            self._assert_linters_pass(td)

    def test_unknown_mode_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _git_init(td)
            with self.assertRaises(ValueError):
                self._scaffold(td, "mainframe")

    def test_cli_unknown_mode_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            _git_init(td)
            res = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "init_project.py"), "--dir", td, "--ci-mode", "mainframe"],
                cwd=str(REPO_ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)

    def test_scaffolded_config_passes_validator(self):
        from tools import common
        with tempfile.TemporaryDirectory() as td:
            _git_init(td)
            self._scaffold(td, "self-hosted-runner")
            cfg, findings = common.load_aesop_config(Path(td))
            self.assertEqual(findings, [])
            self.assertEqual(cfg["ci"]["mode"], ["self-hosted-runner"])


class TestDocsClaimRealCommands(unittest.TestCase):
    """docs/CI-MODES.md may only cite commands and flags `node bin/cli.js --help` shows."""

    def test_every_cited_cli_line_is_in_help(self):
        doc = (REPO_ROOT / "docs" / "CI-MODES.md").read_text(encoding="utf-8")
        help_out = subprocess.run(
            ["node", str(REPO_ROOT / "bin" / "cli.js"), "--help"],
            cwd=str(REPO_ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=30).stdout
        # Commands are CLAIMED inside fenced code blocks; prose mentions are not claims.
        fenced = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", doc, re.S))
        self.assertTrue(fenced.strip(), "docs/CI-MODES.md has no fenced code blocks")
        cited = re.findall(r"^\s*(?:npx @matt82198/aesop|aesop) ([a-z][a-z-]*(?: [a-z][a-z-]*)?)([^\n]*)$", fenced, re.M)
        self.assertTrue(cited, "docs/CI-MODES.md cites no CLI commands at all")
        for command, rest in cited:
            first = command.split()[0]
            self.assertIn(first, help_out, "command %r not in --help" % first)
            for flag in re.findall(r"--[a-z][a-z-]*", rest):
                self.assertIn(flag, help_out, "flag %r not in --help" % flag)


if __name__ == "__main__":
    unittest.main()
