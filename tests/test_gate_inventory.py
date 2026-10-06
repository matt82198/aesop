"""Tests for tools/gate_inventory.py -- exhaustive gate-invoker inventory.

Both axes are exercised failing-first against synthetic repos built in a tempdir:
an orphan gate tool must be reported (axis 1) and a pre-push check documented in
hooks/CLAUDE.md but never called in hooks/pre-push-policy.sh must be reported
(axis 2). Fixtures are real git repos so `git ls-files` behaves as it does in
production; nothing touches the developer's cwd or global git config.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, "tools", "gate_inventory.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import gate_inventory  # noqa: E402


PREPUSH_HEADER = """#!/usr/bin/env bash
set -euo pipefail
"""

HOOKS_CLAUDEMD_TEMPLATE = """# hooks/ -- Git policy enforcement

## pre-push-policy.sh

**Checks & Exit Contract**:
{checks}

## pre-commit-waveguard.sh

Unrelated section; `check_never_documented_here()` must not be attributed above.
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run_git(repo, *args):
    subprocess.run(
        ["git", "-C", repo] + list(args),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def run_tool(repo, extra=None):
    """Invoke the tool as a subprocess in the fixture repo; returns (rc, stdout, stderr)."""
    cmd = [sys.executable, TOOL, "--check", "--root", repo]
    if extra:
        cmd.extend(extra)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        cwd=repo,
    )
    return proc.returncode, proc.stdout, proc.stderr


class FixtureRepo:
    """Minimal git repo with the surfaces gate_inventory.py reads."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="gate-inv-")
        run_git(self.root, "init", "-q")
        # Repo-local identity only -- never the global config.
        run_git(self.root, "config", "user.email", "fixture@example.invalid")
        run_git(self.root, "config", "user.name", "Fixture")

    def add_gate_tool(self, name):
        write(os.path.join(self.root, "tools", name), "#!/usr/bin/env python3\n")

    def set_workflow(self, body):
        write(os.path.join(self.root, ".github", "workflows", "ci.yml"), body)

    def set_cli(self, body):
        write(os.path.join(self.root, "bin", "cli.js"), body)

    def set_prepush(self, body):
        write(os.path.join(self.root, "hooks", "pre-push-policy.sh"), body)

    def set_hooks_claudemd(self, body):
        write(os.path.join(self.root, "hooks", "CLAUDE.md"), body)

    def set_allowlist(self, obj):
        write(
            os.path.join(self.root, "tools", "gate-inventory-allowlist.json"),
            json.dumps(obj, indent=2) + "\n",
        )

    def commit(self):
        run_git(self.root, "add", "-A")
        run_git(self.root, "commit", "-q", "-m", "fixture")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def default_prepush(checks):
    """Hook where every named check is both defined AND called from main()."""
    parts = [PREPUSH_HEADER]
    for name in checks:
        parts.append("%s() {\n  return 0\n}\n" % name)
    parts.append("main() {\n")
    for name in checks:
        parts.append("  if ! %s; then exit 1; fi\n" % name)
    parts.append("}\n")
    return "".join(parts)


def claudemd_for(checks):
    lines = []
    for idx, name in enumerate(checks, 1):
        lines.append("%d. `%s()` -- documented pre-push check; exit 1 on violation" % (idx, name))
    return HOOKS_CLAUDEMD_TEMPLATE.format(checks="\n".join(lines))


class BaseInventoryTest(unittest.TestCase):
    """Builds a green baseline fixture; each test perturbs one thing."""

    CHECKS = ["check_branch_policy", "check_secret_scan", "check_metrics"]

    def setUp(self):
        self.fx = FixtureRepo()
        self.addCleanup(self.fx.cleanup)
        self.fx.add_gate_tool("alpha_lint.py")
        self.fx.add_gate_tool("beta_check.py")
        self.fx.add_gate_tool("verify_gamma.py")
        self.fx.set_workflow(
            "jobs:\n  ci:\n    steps:\n"
            "      - run: python tools/alpha_lint.py --check\n"
            "      - run: python tools/verify_gamma.py\n"
        )
        self.fx.set_cli("const T = { beta: 'tools/beta_check.py' };\n")
        self.fx.set_prepush(default_prepush(self.CHECKS))
        self.fx.set_hooks_claudemd(claudemd_for(self.CHECKS))
        self.fx.commit()


class TestBaseline(BaseInventoryTest):
    def test_green_baseline_exits_zero(self):
        rc, out, err = run_tool(self.fx.root)
        self.assertEqual(rc, 0, "baseline should pass\n%s\n%s" % (out, err))
        self.assertIn("RESULT: PASS", out)

    def test_json_report_shape(self):
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["findings"], 0)
        self.assertEqual(data["axis1"]["total"], 3)
        self.assertEqual(data["axis2"]["total"], len(self.CHECKS))
        kinds = {r["tool"]: r["invoker_kind"] for r in data["axis1"]["resolved"]}
        self.assertEqual(kinds["tools/alpha_lint.py"], "ci-workflow")
        self.assertEqual(kinds["tools/beta_check.py"], "cli")


class TestAxis1Orphans(BaseInventoryTest):
    """Failing-first: a gate tool nobody invokes must be reported."""

    def test_orphan_gate_tool_is_reported(self):
        self.fx.add_gate_tool("lonely_check.py")
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("ORPHAN", out)
        self.assertIn("tools/lonely_check.py", out)

    def test_orphan_reported_for_every_gate_suffix(self):
        for name in ("lonely_lint.py", "lonely_check.py", "lonely_gate.py", "verify_lonely.py"):
            self.fx.add_gate_tool(name)
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        orphans = {f["tool"] for f in data["axis1"]["findings"]}
        self.assertEqual(
            orphans,
            {
                "tools/lonely_lint.py",
                "tools/lonely_check.py",
                "tools/lonely_gate.py",
                "tools/verify_lonely.py",
            },
        )

    def test_non_gate_shaped_tool_is_not_enumerated(self):
        write(os.path.join(self.fx.root, "tools", "helper_util.py"), "x = 1\n")
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["axis1"]["total"], 3)

    def test_git_hook_counts_as_invoker(self):
        self.fx.add_gate_tool("hooked_check.py")
        self.fx.set_prepush(
            default_prepush(self.CHECKS)
            + '\nrun_hooked() { python "$ROOT/tools/hooked_check.py" --check; }\n'
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        kinds = {r["tool"]: r["invoker_kind"] for r in json.loads(out)["axis1"]["resolved"]}
        self.assertEqual(kinds["tools/hooked_check.py"], "git-hook")

    def test_one_hop_tool_chain_counts_as_invoker(self):
        self.fx.add_gate_tool("chained_check.py")
        write(
            os.path.join(self.fx.root, "tools", "driver_tool.py"),
            'import subprocess\nsubprocess.run(["python", "tools/chained_check.py"])\n',
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        kinds = {r["tool"]: r["invoker_kind"] for r in json.loads(out)["axis1"]["resolved"]}
        self.assertEqual(kinds["tools/chained_check.py"], "tool-chain")

    def test_tool_named_only_in_a_yaml_comment_is_still_an_orphan(self):
        """ci.yml really does carry `# PyYAML is required by tools/ci_workflow_lint.py`.

        A stray remark must not count as wiring.
        """
        self.fx.add_gate_tool("mentioned_check.py")
        self.fx.set_workflow(
            "jobs:\n  ci:\n    steps:\n"
            "      # PyYAML is required by tools/mentioned_check.py\n"
            "      - run: python tools/alpha_lint.py --check\n"
            "      - run: python tools/verify_gamma.py\n"
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("tools/mentioned_check.py", out)

    def test_tool_named_only_in_a_js_comment_is_still_an_orphan(self):
        self.fx.add_gate_tool("jsmentioned_check.py")
        self.fx.set_cli(
            "// see tools/jsmentioned_check.py for details\n"
            "const T = { beta: 'tools/beta_check.py' };\n"
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("tools/jsmentioned_check.py", out)

    def test_test_file_reference_is_not_an_invoker(self):
        """A tool imported only by its own test suite is still an orphan."""
        self.fx.add_gate_tool("tested_check.py")
        write(
            os.path.join(self.fx.root, "tests", "test_tested_check.py"),
            "import tested_check\n",
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("tools/tested_check.py", out)

    def test_untracked_gate_tool_is_not_enumerated(self):
        """Enumeration is git-tracked-only, so scratch files do not trip the gate."""
        self.fx.add_gate_tool("scratch_check.py")  # deliberately not committed
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["axis1"]["total"], 3)

    def test_tool_is_not_its_own_invoker(self):
        """A gate tool whose only mention of itself is its own usage string is an orphan."""
        write(
            os.path.join(self.fx.root, "tools", "selfref_check.py"),
            '"""usage: python tools/selfref_check.py --check"""\n',
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("tools/selfref_check.py", out)


class TestAllowlist(BaseInventoryTest):
    def test_allowlist_entry_with_reason_resolves_orphan(self):
        self.fx.add_gate_tool("operator_check.py")
        self.fx.set_allowlist(
            {
                "version": 1,
                "entries": {
                    "operator_check.py": {
                        "reason": "operator-invoked only: run by hand during incident triage"
                    }
                },
            }
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        entry = [
            r
            for r in json.loads(out)["axis1"]["resolved"]
            if r["tool"] == "tools/operator_check.py"
        ][0]
        self.assertEqual(entry["invoker_kind"], "allowlist")
        self.assertIn("operator-invoked only", entry["reason"])

    def test_allowlist_without_reason_is_rejected(self):
        self.fx.add_gate_tool("operator_check.py")
        self.fx.set_allowlist(
            {"version": 1, "entries": {"operator_check.py": {"reason": ""}}}
        )
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOWLIST-NO-REASON", out)

    def test_allowlist_with_stub_reason_is_rejected(self):
        self.fx.add_gate_tool("operator_check.py")
        self.fx.set_allowlist({"version": 1, "entries": {"operator_check.py": "n/a"}})
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOWLIST-NO-REASON", out)

    def test_malformed_allowlist_fails_closed(self):
        write(
            os.path.join(self.fx.root, "tools", "gate-inventory-allowlist.json"),
            "{not json",
        )
        self.fx.commit()
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("not valid JSON", err)


class TestAxis2DocumentedChecks(BaseInventoryTest):
    """Failing-first: a documented pre-push check with no call site must be reported."""

    def test_documented_but_never_called_is_reported(self):
        checks = self.CHECKS + ["check_orphaned_gate"]
        # Defined in the hook but never called from main().
        hook = default_prepush(self.CHECKS) + "\ncheck_orphaned_gate() {\n  return 0\n}\n"
        self.fx.set_prepush(hook)
        self.fx.set_hooks_claudemd(claudemd_for(checks))
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("DOCUMENTED-NOT-INVOKED", out)
        self.assertIn("check_orphaned_gate", out)

    def test_documented_but_entirely_absent_is_reported(self):
        checks = self.CHECKS + ["check_totally_missing"]
        self.fx.set_hooks_claudemd(claudemd_for(checks))
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 1)
        names = {f["check"] for f in json.loads(out)["axis2"]["findings"]}
        self.assertEqual(names, {"check_totally_missing"})

    def test_substring_match_does_not_satisfy_a_documented_check(self):
        """Regression: three prior defects in this repo came from substring matching.

        `check_metrics_extended` being called must NOT satisfy documented
        `check_metrics`.
        """
        checks = ["check_branch_policy", "check_secret_scan", "check_metrics"]
        hook = default_prepush(["check_branch_policy", "check_secret_scan"])
        hook += "\ncheck_metrics_extended() {\n  return 0\n}\ncheck_metrics_extended\n"
        self.fx.set_prepush(hook)
        self.fx.set_hooks_claudemd(claudemd_for(checks))
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("check_metrics", out)

    def test_definition_alone_is_not_a_call_site(self):
        hook = PREPUSH_HEADER + "check_branch_policy() {\n  return 0\n}\n"
        hook += "check_secret_scan() {\n  return 0\n}\ncheck_secret_scan\n"
        hook += "check_metrics() {\n  return 0\n}\ncheck_metrics\n"
        self.fx.set_prepush(hook)
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("check_branch_policy", out)

    def test_commented_out_call_is_not_a_call_site(self):
        hook = default_prepush(["check_branch_policy", "check_secret_scan"])
        hook += "\ncheck_metrics() {\n  return 0\n}\n# check_metrics  disabled for now\n"
        self.fx.set_prepush(hook)
        self.fx.commit()
        rc, out, _ = run_tool(self.fx.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("check_metrics", out)

    def test_checks_from_other_hook_sections_are_not_attributed(self):
        """`check_never_documented_here()` lives under pre-commit-waveguard, not pre-push."""
        rc, out, _ = run_tool(self.fx.root, ["--json"])
        self.assertEqual(rc, 0, out)
        names = {r["check"] for r in json.loads(out)["axis2"]["resolved"]}
        self.assertNotIn("check_never_documented_here", names)


class TestFailClosed(BaseInventoryTest):
    def test_missing_hooks_claudemd_exits_two(self):
        os.remove(os.path.join(self.fx.root, "hooks", "CLAUDE.md"))
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("hooks", err)

    def test_missing_prepush_script_exits_two(self):
        os.remove(os.path.join(self.fx.root, "hooks", "pre-push-policy.sh"))
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("pre-push-policy.sh", err)

    def test_claudemd_without_prepush_section_exits_two(self):
        self.fx.set_hooks_claudemd("# hooks\n\n## something-else\n\nno checks here\n")
        self.fx.commit()
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("pre-push-policy.sh", err)

    def test_claudemd_documenting_no_checks_exits_two(self):
        """A vacuously-green axis 2 is an error, not a pass."""
        self.fx.set_hooks_claudemd(
            "# hooks\n\n## pre-push-policy.sh\n\nProse with no check names.\n"
        )
        self.fx.commit()
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("vacuously green", err)

    def test_empty_gate_inventory_exits_two(self):
        for name in ("alpha_lint.py", "beta_check.py", "verify_gamma.py"):
            os.remove(os.path.join(self.fx.root, "tools", name))
        self.fx.commit()
        rc, _, err = run_tool(self.fx.root)
        self.assertEqual(rc, 2)
        self.assertIn("inventory cannot be empty", err)

    def test_nonexistent_root_exits_two(self):
        rc = gate_inventory.main(
            ["--check", "--root", os.path.join(self.fx.root, "no-such-dir")]
        )
        self.assertEqual(rc, 2)

    def test_unknown_flag_exits_two(self):
        proc = subprocess.run(
            [sys.executable, TOOL, "--check", "--bogus-flag"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            cwd=self.fx.root,
        )
        self.assertEqual(proc.returncode, 2)


class TestUnitHelpers(unittest.TestCase):
    """Direct unit coverage of the matching primitives."""

    def test_word_boundary_call_site_detection(self):
        hook = "check_metrics_extended() {\n  :\n}\ncheck_metrics_extended\n"
        self.assertEqual(gate_inventory.find_call_sites(hook, "check_metrics"), [])
        self.assertEqual(
            gate_inventory.find_call_sites(hook, "check_metrics_extended"), [4]
        )

    def test_definition_line_excluded_from_call_sites(self):
        hook = "check_x() {\n  :\n}\n"
        self.assertEqual(gate_inventory.find_call_sites(hook, "check_x"), [])

    def test_references_tool_matches_path_and_bare_name(self):
        self.assertTrue(
            gate_inventory.references_tool("run: python tools/foo_check.py", "foo_check.py", "foo_check")
        )
        self.assertTrue(
            gate_inventory.references_tool('T = "foo_check.py"', "foo_check.py", "foo_check")
        )
        self.assertTrue(
            gate_inventory.references_tool("import foo_check\n", "foo_check.py", "foo_check")
        )
        self.assertTrue(
            gate_inventory.references_tool(
                "from tools.foo_check import main\n", "foo_check.py", "foo_check"
            )
        )

    def test_references_tool_rejects_longer_neighbour(self):
        self.assertFalse(
            gate_inventory.references_tool(
                "run: python tools/xfoo_check.py", "foo_check.py", "foo_check"
            )
        )

    def test_extract_documented_checks_scopes_to_prepush_section(self):
        text = claudemd_for(["check_a", "check_b"])
        names = gate_inventory.extract_documented_checks(text)
        self.assertEqual(names, ["check_a", "check_b"])

    def test_strip_shell_comments_removes_full_line_comments(self):
        stripped = gate_inventory.strip_shell_comments("# check_x\nreal_line\n")
        self.assertNotIn("check_x", stripped)
        self.assertIn("real_line", stripped)


if __name__ == "__main__":
    unittest.main()
