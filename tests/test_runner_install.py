#!/usr/bin/env python3
"""Tests for tools/runner_install.py (`aesop runner install|remove`).

Nothing here installs anything: `gh` and the capability probe are injected,
every path is exercised through the plan builder, and the only subprocess is
the CLI itself in --dry-run.
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

from tools import runner_install as ri  # noqa: E402


def _probes(blocked=True):
    return {
        "os": {"system": "Windows", "release": "11", "version": "10.0.26200"},
        "cpu_cores": 8, "ram_gb": 32.0,
        "windows_code_integrity": {
            "smart_app_control_state": 1 if blocked else 0,
            "umci_enforcement_status": 2 if blocked else 0,
        },
        "wsl": {"present": True}, "docker": {"present": True},
        "gh": {"present": True, "authenticated": True},
        "cloudflared": {"present": False},
        "receipt_tools": {"emit_receipt": False, "verify_receipt": False, "verify_workflow": False},
    }


def _sha_body(sha):
    return ("Release notes\n\n<!-- BEGIN SHA win-x64 -->" + sha + "<!-- END SHA win-x64 -->\n"
            "<!-- BEGIN SHA linux-x64 -->" + ("b" * 64) + "<!-- END SHA linux-x64 -->\n")


class FakeGh:
    """Scripted `gh api` responder; records every call."""

    def __init__(self, visibility="private", policy="first_time_contributors", sha="a" * 64, fail_on=None):
        self.calls = []
        self.visibility = visibility
        self.policy = policy
        self.sha = sha
        self.fail_on = fail_on or set()

    def __call__(self, args):
        self.calls.append(list(args))
        joined = " ".join(args)
        for needle in self.fail_on:
            if needle in joined:
                return 1, "", "gh: HTTP 404"
        if "fork-pr-contributor-approval" in joined and "-X" in args and "PUT" in args:
            self.policy = "all_external_contributors"
            return 0, "{}", ""
        if "fork-pr-contributor-approval" in joined:
            return 0, json.dumps({"approval_policy": self.policy}), ""
        if joined.endswith("repos/acme/widgets") or joined.endswith("repos/acme/widgets --jq .visibility"):
            return 0, json.dumps({"visibility": self.visibility, "full_name": "acme/widgets"}), ""
        if "repos/actions/runner/releases/latest" in joined:
            return 0, json.dumps({
                "tag_name": "v2.300.0",
                "body": _sha_body(self.sha),
                "assets": [
                    {"name": "actions-runner-win-x64-2.300.0.zip",
                     "browser_download_url": "https://github.com/actions/runner/releases/download/v2.300.0/actions-runner-win-x64-2.300.0.zip"},
                    {"name": "actions-runner-linux-x64-2.300.0.tar.gz",
                     "browser_download_url": "https://github.com/actions/runner/releases/download/v2.300.0/actions-runner-linux-x64-2.300.0.tar.gz"},
                ],
            }), ""
        if "registration-token" in joined:
            return 0, json.dumps({"token": "REGTOKEN" + "SECRET" * 4}), ""
        if "remove-token" in joined:
            return 0, json.dumps({"token": "REMTOKEN" + "SECRET" * 4}), ""
        return 1, "", "unscripted gh call: " + joined


class TestPreflight(unittest.TestCase):
    """Refusal paths come first; nothing downstream runs when preflight refuses."""

    def test_refuses_when_code_integrity_blocks_runner(self):
        gh = FakeGh()
        with self.assertRaises(ri.Refusal) as ctx:
            ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=True), gh=gh)
        self.assertIn("Smart App Control", str(ctx.exception))
        self.assertEqual(gh.calls, [], "no gh call may happen once the box is known to block the runner")

    def test_refuses_public_repo_without_all_external_contributors_policy(self):
        gh = FakeGh(visibility="public", policy="first_time_contributors")
        with self.assertRaises(ri.Refusal) as ctx:
            ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=False), gh=gh)
        msg = str(ctx.exception)
        self.assertIn("all_external_contributors", msg)
        self.assertIn("--set-fork-policy", msg)

    def test_set_fork_policy_puts_then_proceeds(self):
        gh = FakeGh(visibility="public", policy="first_time_contributors")
        plan = ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=False),
                                     gh=gh, set_fork_policy=True)
        puts = [c for c in gh.calls if "PUT" in c and "fork-pr-contributor-approval" in " ".join(c)]
        self.assertEqual(len(puts), 1)
        self.assertIn("approval_policy=all_external_contributors", " ".join(puts[0]))
        self.assertEqual(plan["repo"]["fork_pr_policy"], "all_external_contributors")

    def test_private_repo_skips_fork_policy_requirement(self):
        gh = FakeGh(visibility="private", policy="first_time_contributors")
        plan = ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=False), gh=gh)
        self.assertEqual(plan["repo"]["visibility"], "private")

    def test_unauthenticated_gh_refused(self):
        probes = _probes(blocked=False)
        probes["gh"] = {"present": True, "authenticated": False}
        with self.assertRaises(ri.Refusal) as ctx:
            ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=probes, gh=FakeGh())
        self.assertIn("gh auth", str(ctx.exception))

    def test_bad_instances_rejected(self):
        with self.assertRaises(ri.Refusal):
            ri.build_install_plan("acme/widgets", instances=0, labels=None, probes=_probes(blocked=False), gh=FakeGh())


class TestPlan(unittest.TestCase):
    """The documented GitHub steps, rendered as a plan."""

    def setUp(self):
        self.gh = FakeGh()
        self.plan = ri.build_install_plan("acme/widgets", instances=3, labels=["self-hosted", "windows", "aesop-box"],
                                          probes=_probes(blocked=False), gh=self.gh, install_root="/runners")

    def test_release_asset_and_sha_from_release_notes(self):
        self.assertEqual(self.plan["release"]["tag"], "v2.300.0")
        self.assertTrue(self.plan["release"]["asset"].startswith("actions-runner-win-x64-"))
        self.assertEqual(self.plan["release"]["sha256"], "a" * 64)

    def test_n_instances_in_sibling_dirs(self):
        dirs = [inst["dir"] for inst in self.plan["instances"]]
        self.assertEqual(len(dirs), 3)
        self.assertEqual(len(set(dirs)), 3)
        self.assertTrue(all(d.replace("\\", "/").startswith("/runners/") for d in dirs), dirs)
        names = [inst["name"] for inst in self.plan["instances"]]
        self.assertEqual(len(set(names)), 3)

    def test_config_command_shape(self):
        cmd = self.plan["instances"][0]["config_cmd"]
        self.assertIn("--unattended", cmd)
        self.assertIn("--runasservice", cmd)
        self.assertIn("--labels", cmd)
        self.assertIn("self-hosted,windows,aesop-box", cmd)
        self.assertIn("--url", cmd)
        self.assertIn("https://github.com/acme/widgets", cmd)
        self.assertIn("--token", cmd)
        self.assertIn(ri.TOKEN_PLACEHOLDER, cmd)
        self.assertNotIn("REGTOKEN", " ".join(cmd))

    def test_plan_never_fetches_registration_token(self):
        self.assertFalse(any("registration-token" in " ".join(c) for c in self.gh.calls),
                         "a registration token is single-use: the plan must not spend one")

    def test_render_plan_has_no_secret(self):
        text = ri.render_plan(self.plan)
        self.assertNotIn("SECRET", text)
        self.assertIn("sha256", text)
        self.assertIn("v2.300.0", text)

    def test_sha_mismatch_refused(self):
        with self.assertRaises(ri.Refusal) as ctx:
            ri.verify_sha256(b"not the archive", "a" * 64)
        self.assertIn("sha256", str(ctx.exception).lower())

    def test_sha_missing_from_release_notes_refused(self):
        gh = FakeGh(sha="")
        gh.sha = ""
        with self.assertRaises(ri.Refusal) as ctx:
            ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=False), gh=gh)
        self.assertIn("sha256", str(ctx.exception).lower())

    def test_default_labels_when_none_given(self):
        plan = ri.build_install_plan("acme/widgets", instances=1, labels=None, probes=_probes(blocked=False), gh=FakeGh())
        self.assertEqual(plan["labels"], ri.DEFAULT_LABELS)


class TestRemove(unittest.TestCase):
    """`aesop runner remove` plans the removal-token path."""

    def test_remove_plan_uses_remove_token_endpoint_without_printing_it(self):
        gh = FakeGh()
        plan = ri.build_remove_plan("acme/widgets", instances=2, gh=gh, install_root="/runners")
        self.assertEqual(len(plan["instances"]), 2)
        self.assertIn("remove", plan["instances"][0]["config_cmd"])
        self.assertIn(ri.TOKEN_PLACEHOLDER, plan["instances"][0]["config_cmd"])
        self.assertFalse(any("remove-token" in " ".join(c) for c in gh.calls))
        self.assertNotIn("REMTOKEN", ri.render_plan(plan))

    def test_fetch_token_is_the_only_place_a_token_is_seen(self):
        gh = FakeGh()
        token = ri.fetch_token("acme/widgets", "registration-token", gh=gh)
        self.assertTrue(token.startswith("REGTOKEN"))
        self.assertEqual(gh.calls[-1][:3], ["api", "-X", "POST"])


class TestCliDryRun(unittest.TestCase):
    """The real CLI with an injected probe fixture; never installs."""

    def _run(self, args, probes):
        with tempfile.TemporaryDirectory() as td:
            fx = Path(td) / "probes.json"
            fx.write_text(json.dumps(probes), encoding="utf-8")
            env = dict(os.environ)
            env["AESOP_CI_PROBE_FIXTURE"] = str(fx)
            return subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "runner_install.py"), *args],
                cwd=str(REPO_ROOT), env=env, capture_output=True,
                encoding="utf-8", errors="replace", timeout=60)

    def test_dry_run_refused_on_blocked_box(self):
        res = self._run(["install", "--dry-run", "--repo", "acme/widgets"], _probes(blocked=True))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("Smart App Control", res.stdout + res.stderr)
        self.assertIn("REFUSED", res.stdout + res.stderr)

    def test_unknown_subcommand_exits_2(self):
        res = self._run(["explode"], _probes(blocked=False))
        self.assertEqual(res.returncode, 2)

    def test_help_mentions_documented_flags(self):
        res = self._run(["--help"], _probes(blocked=False))
        self.assertEqual(res.returncode, 0)
        for flag in ("--instances", "--labels", "--dry-run", "--set-fork-policy", "--repo"):
            self.assertIn(flag, res.stdout)


if __name__ == "__main__":
    unittest.main()
