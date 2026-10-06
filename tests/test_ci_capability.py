#!/usr/bin/env python3
"""Tests for tools/ci_capability.py -- the `aesop doctor` ci-capability section.

Probe results are INJECTED (no registry/PowerShell/gh on the test path); the
mode table is a pure function of them. The section is report-only: no injected
state may turn into a failing exit code.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import ci_capability as cc  # noqa: E402


def _probes(**overrides):
    base = {
        "os": {"system": "Windows", "release": "11", "version": "10.0.26200"},
        "cpu_cores": 32,
        "ram_gb": 63.6,
        "windows_code_integrity": {
            "smart_app_control_state": 1,
            "umci_enforcement_status": 2,
        },
        "wsl": {"present": True},
        "docker": {"present": True},
        "gh": {"present": True, "authenticated": True},
        "cloudflared": {"present": False},
        "receipt_tools": {
            "emit_receipt": False,
            "verify_receipt": False,
            "verify_workflow": False,
        },
    }
    base.update(overrides)
    return base


class TestModeEvaluation(unittest.TestCase):
    """Pure evaluation of injected probe results."""

    def test_sac_enforced_blocks_self_hosted_runner(self):
        modes = {m["mode"]: m for m in cc.evaluate_modes(_probes())}
        self.assertFalse(modes["self-hosted-runner"]["runnable"])
        self.assertIn("Smart App Control", modes["self-hosted-runner"]["why"])

    def test_umci_enforced_alone_blocks_self_hosted_runner(self):
        probes = _probes(windows_code_integrity={"smart_app_control_state": 0, "umci_enforcement_status": 2})
        modes = {m["mode"]: m for m in cc.evaluate_modes(probes)}
        self.assertFalse(modes["self-hosted-runner"]["runnable"])
        self.assertIn("code integrity", modes["self-hosted-runner"]["why"].lower())

    def test_windows_without_enforcement_allows_self_hosted_runner(self):
        probes = _probes(windows_code_integrity={"smart_app_control_state": 0, "umci_enforcement_status": 0})
        modes = {m["mode"]: m for m in cc.evaluate_modes(probes)}
        self.assertTrue(modes["self-hosted-runner"]["runnable"], modes["self-hosted-runner"]["why"])

    def test_linux_box_allows_self_hosted_runner(self):
        probes = _probes(os={"system": "Linux", "release": "6.8", "version": "#1 SMP"}, windows_code_integrity=None)
        modes = {m["mode"]: m for m in cc.evaluate_modes(probes)}
        self.assertTrue(modes["self-hosted-runner"]["runnable"])

    def test_unauthenticated_gh_blocks_self_hosted_runner(self):
        probes = _probes(
            windows_code_integrity={"smart_app_control_state": 0, "umci_enforcement_status": 0},
            gh={"present": True, "authenticated": False})
        modes = {m["mode"]: m for m in cc.evaluate_modes(probes)}
        self.assertFalse(modes["self-hosted-runner"]["runnable"])
        self.assertIn("gh auth", modes["self-hosted-runner"]["why"])

    def test_hosted_is_always_runnable(self):
        modes = {m["mode"]: m for m in cc.evaluate_modes(_probes(gh={"present": False, "authenticated": False}))}
        self.assertTrue(modes["hosted"]["runnable"])

    def test_receipt_gate_pending_until_receipt_tools_present(self):
        modes = {m["mode"]: m for m in cc.evaluate_modes(_probes())}
        self.assertFalse(modes["local-receipt-gate"]["runnable"])
        self.assertIn("emit_receipt.py", modes["local-receipt-gate"]["why"])
        present = _probes(receipt_tools={"emit_receipt": True, "verify_receipt": True, "verify_workflow": True})
        modes = {m["mode"]: m for m in cc.evaluate_modes(present)}
        self.assertTrue(modes["local-receipt-gate"]["runnable"], modes["local-receipt-gate"]["why"])

    def test_receipt_gate_needs_linux_shard_capability_on_windows(self):
        probes = _probes(
            wsl={"present": False}, docker={"present": False},
            receipt_tools={"emit_receipt": True, "verify_receipt": True, "verify_workflow": True})
        modes = {m["mode"]: m for m in cc.evaluate_modes(probes)}
        self.assertFalse(modes["local-receipt-gate"]["runnable"])
        self.assertIn("WSL", modes["local-receipt-gate"]["why"])

    def test_every_mode_reported_exactly_once(self):
        names = [m["mode"] for m in cc.evaluate_modes(_probes())]
        self.assertEqual(sorted(names), sorted(cc.CI_MODES))
        self.assertEqual(len(names), len(set(names)))

    def test_runner_block_reason_shared_with_runner_install(self):
        blocked, reason = cc.runner_blocked(_probes())
        self.assertTrue(blocked)
        self.assertIn("Smart App Control", reason)
        ok, reason = cc.runner_blocked(_probes(windows_code_integrity={"smart_app_control_state": 0, "umci_enforcement_status": 1}))
        self.assertFalse(ok, reason)


class TestProbeParsing(unittest.TestCase):
    """Parsers for the raw Windows probe output."""

    def test_parse_reg_query_dword(self):
        out = ("\r\nHKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy\r\n"
               "    VerifiedAndReputablePolicyState    REG_DWORD    0x1\r\n\r\n")
        self.assertEqual(cc.parse_reg_dword(out, "VerifiedAndReputablePolicyState"), 1)
        self.assertIsNone(cc.parse_reg_dword("ERROR: The system was unable to find the specified registry key", "X"))

    def test_parse_int_output(self):
        self.assertEqual(cc.parse_int_output(" 2 \r\n"), 2)
        self.assertIsNone(cc.parse_int_output(""))
        self.assertIsNone(cc.parse_int_output("Get-CimInstance : Invalid namespace"))

    def test_interpretations(self):
        self.assertEqual(cc.describe_sac(0), "off")
        self.assertEqual(cc.describe_sac(1), "enforced")
        self.assertEqual(cc.describe_sac(2), "evaluation")
        self.assertEqual(cc.describe_umci(2), "enforced")
        self.assertEqual(cc.describe_umci(1), "audit")
        self.assertEqual(cc.describe_umci(None), "unknown")


class TestCli(unittest.TestCase):
    """The CLI consumes an injected fixture and never fails on capability gaps."""

    def _run(self, args, fixture):
        with tempfile.TemporaryDirectory() as td:
            fx = Path(td) / "probes.json"
            fx.write_text(json.dumps(fixture), encoding="utf-8")
            env = dict(os.environ)
            env["AESOP_CI_PROBE_FIXTURE"] = str(fx)
            return subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "ci_capability.py"), *args],
                cwd=str(REPO_ROOT), env=env, capture_output=True,
                encoding="utf-8", errors="replace", timeout=60)

    def test_json_output_shape_and_zero_exit(self):
        res = self._run(["--json"], _probes())
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(sorted(m["mode"] for m in data["modes"]), sorted(cc.CI_MODES))
        self.assertEqual(data["probes"]["cpu_cores"], 32)
        self.assertEqual(data["windows"]["smart_app_control"], "enforced")
        self.assertEqual(data["windows"]["umci"], "enforced")
        self.assertTrue(data["windows"]["runner_blocked"])
        for mode in data["modes"]:
            self.assertIn("runnable", mode)
            self.assertIn("why", mode)

    def test_text_table_lists_every_mode(self):
        res = self._run([], _probes())
        self.assertEqual(res.returncode, 0, res.stderr)
        for mode in cc.CI_MODES:
            self.assertIn(mode, res.stdout)
        self.assertIn("runnable here", res.stdout)

    def test_unknown_flag_exits_2(self):
        res = self._run(["--bogus"], _probes())
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
