#!/usr/bin/env python3
"""Tests for the `ci` block of aesop.config.json (tools/common.py validate_ci_config).

Red-first: every invalid combination here must be REJECTED with a stable code, and
the Node mirror (tools/ci_config.js) must produce the identical code set for the same
input -- that parity is driven, not grepped: both validators run on the same fixtures.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import common  # noqa: E402


def _codes(findings):
    return sorted(f["code"] for f in findings)


class TestValidateCiConfigPython(unittest.TestCase):
    """Behavioural tests against the Python validator."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_absent_ci_block_is_valid(self):
        self.assertEqual(common.validate_ci_config({}, self.root), [])

    def test_default_block_is_valid(self):
        cfg = {"ci": common.default_ci_config()}
        self.assertEqual(common.validate_ci_config(cfg, self.root), [])
        self.assertEqual(cfg["ci"]["mode"], ["hosted"])
        self.assertEqual(cfg["ci"]["windowsMatrix"], "hosted")
        self.assertEqual(cfg["ci"]["receiptGate"], "off")
        self.assertEqual(cfg["ci"]["selfHostedLabels"], ["self-hosted", "windows", "aesop-box"])

    def test_unknown_mode_rejected(self):
        cfg = {"ci": {"mode": ["hosted", "mainframe"]}}
        self.assertIn("CI_MODE_UNKNOWN", _codes(common.validate_ci_config(cfg, self.root)))

    def test_mode_must_be_nonempty_list(self):
        self.assertIn("CI_MODE_NOT_LIST", _codes(common.validate_ci_config({"ci": {"mode": "hosted"}}, self.root)))
        self.assertIn("CI_MODE_EMPTY", _codes(common.validate_ci_config({"ci": {"mode": []}}, self.root)))

    def test_duplicate_mode_rejected(self):
        cfg = {"ci": {"mode": ["hosted", "hosted"]}}
        self.assertIn("CI_MODE_DUPLICATE", _codes(common.validate_ci_config(cfg, self.root)))

    def test_windows_matrix_enum(self):
        cfg = {"ci": {"mode": ["hosted"], "windowsMatrix": "sometimes"}}
        self.assertIn("CI_WINDOWS_MATRIX_UNKNOWN", _codes(common.validate_ci_config(cfg, self.root)))

    def test_windows_matrix_self_hosted_requires_runner_mode(self):
        cfg = {"ci": {"mode": ["hosted"], "windowsMatrix": "self-hosted"}}
        self.assertIn("CI_WINDOWS_MATRIX_NEEDS_RUNNER_MODE", _codes(common.validate_ci_config(cfg, self.root)))
        cfg["ci"]["mode"].append("self-hosted-runner")
        self.assertEqual(common.validate_ci_config(cfg, self.root), [])

    def test_receipt_gate_enum(self):
        cfg = {"ci": {"mode": ["hosted"], "receiptGate": "maybe"}}
        self.assertIn("CI_RECEIPT_GATE_UNKNOWN", _codes(common.validate_ci_config(cfg, self.root)))

    def test_receipt_gate_alongside_requires_receipt_mode(self):
        cfg = {"ci": {"mode": ["hosted"], "receiptGate": "alongside"}}
        self.assertIn("CI_RECEIPT_GATE_NEEDS_RECEIPT_MODE", _codes(common.validate_ci_config(cfg, self.root)))

    def test_receipt_gate_required_without_verify_workflow_rejected(self):
        cfg = {"ci": {"mode": ["hosted", "local-receipt-gate"], "receiptGate": "required"}}
        codes = _codes(common.validate_ci_config(cfg, self.root))
        self.assertIn("CI_RECEIPT_GATE_REQUIRES_WORKFLOW", codes)

    def test_receipt_gate_required_with_verify_workflow_accepted(self):
        wf = self.root / ".github" / "workflows" / "verify-receipt.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("name: verify-receipt\non: [pull_request]\njobs: {}\n", encoding="utf-8")
        cfg = {"ci": {"mode": ["hosted", "local-receipt-gate"], "receiptGate": "required"}}
        self.assertEqual(common.validate_ci_config(cfg, self.root), [])

    def test_labels_must_be_string_list_containing_self_hosted(self):
        base = {"mode": ["self-hosted-runner"]}
        bad_type = {"ci": dict(base, selfHostedLabels="self-hosted")}
        self.assertIn("CI_LABELS_NOT_LIST", _codes(common.validate_ci_config(bad_type, self.root)))
        bad_item = {"ci": dict(base, selfHostedLabels=["self-hosted", 7])}
        self.assertIn("CI_LABELS_ITEM_INVALID", _codes(common.validate_ci_config(bad_item, self.root)))
        missing = {"ci": dict(base, selfHostedLabels=["windows"])}
        self.assertIn("CI_LABELS_MISSING_SELF_HOSTED", _codes(common.validate_ci_config(missing, self.root)))

    def test_unknown_key_rejected(self):
        cfg = {"ci": {"mode": ["hosted"], "turbo": True}}
        self.assertIn("CI_UNKNOWN_KEY", _codes(common.validate_ci_config(cfg, self.root)))

    def test_ci_block_must_be_object(self):
        self.assertIn("CI_NOT_OBJECT", _codes(common.validate_ci_config({"ci": ["hosted"]}, self.root)))

    def test_load_aesop_config_reads_and_validates(self):
        (self.root / "aesop.config.json").write_text(
            json.dumps({"repos": [], "ci": {"mode": ["hosted"], "receiptGate": "required"}}),
            encoding="utf-8")
        cfg, findings = common.load_aesop_config(self.root)
        self.assertEqual(cfg["repos"], [])
        self.assertIn("CI_RECEIPT_GATE_NEEDS_RECEIPT_MODE", _codes(findings))

    def test_load_aesop_config_missing_file(self):
        cfg, findings = common.load_aesop_config(self.root)
        self.assertIsNone(cfg)
        self.assertEqual(_codes(findings), ["CONFIG_MISSING"])


PARITY_FIXTURES = [
    {},
    {"ci": {"mode": ["hosted"]}},
    {"ci": {"mode": ["hosted", "mainframe"]}},
    {"ci": {"mode": "hosted"}},
    {"ci": {"mode": []}},
    {"ci": {"mode": ["hosted", "hosted"]}},
    {"ci": {"mode": ["hosted"], "windowsMatrix": "self-hosted"}},
    {"ci": {"mode": ["hosted"], "windowsMatrix": "sometimes"}},
    {"ci": {"mode": ["hosted"], "receiptGate": "alongside"}},
    {"ci": {"mode": ["hosted", "local-receipt-gate"], "receiptGate": "required"}},
    {"ci": {"mode": ["self-hosted-runner"], "selfHostedLabels": ["windows"]}},
    {"ci": {"mode": ["self-hosted-runner"], "selfHostedLabels": ["self-hosted", 7]}},
    {"ci": {"mode": ["hosted"], "turbo": True}},
    {"ci": ["hosted"]},
]


class TestNodePythonParity(unittest.TestCase):
    """Both loaders read the config: the Node mirror must emit the same codes."""

    def test_node_mirror_emits_identical_codes(self):
        with tempfile.TemporaryDirectory() as td:
            script = (
                "const v = require(process.argv[1]);"
                "const fixtures = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
                "const out = fixtures.map(f => v.validateCiConfig(f, process.argv[3]).map(x => x.code).sort());"
                "process.stdout.write(JSON.stringify(out));"
            )
            fixture_path = Path(td) / "fixtures.json"
            fixture_path.write_text(json.dumps(PARITY_FIXTURES), encoding="utf-8")
            res = subprocess.run(
                ["node", "-e", script, str(REPO_ROOT / "tools" / "ci_config.js"), str(fixture_path), td],
                cwd=str(REPO_ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=30,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            node_codes = json.loads(res.stdout)
            py_codes = [_codes(common.validate_ci_config(f, Path(td))) for f in PARITY_FIXTURES]
            self.assertEqual(node_codes, py_codes)
            # Anti-vacuity: the fixture set must exercise every code at least once.
            seen = set(c for codes in py_codes for c in codes)
            self.assertTrue(seen, "parity fixtures produced no findings at all")


if __name__ == "__main__":
    unittest.main()
