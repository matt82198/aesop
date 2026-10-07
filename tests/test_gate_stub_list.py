"""Tests for tools/gate_stub_list.py -- derived TTY-fixture stub list.

Red-first proof: PR #872 took five red CI rounds, each a different gate the
new-gate checklist forgot. One of them was tests/test_pre_push_policy.sh's
"main() TTY guard (option B)" fixture, which used to stub every fail-closed
`check_*` gate script off a hand-maintained bash list -- a list a brand-new
gate is never automatically added to. tools/gate_stub_list.py derives that
list from the real hook source instead. This suite proves:

  1. the derivation matches the real hooks/pre-push-policy.sh exactly (no
     drift between "what main() actually needs stubbed" and "what the tool
     says to stub");
  2. a brand-new `check_zzz()` gate, inserted into a FIXTURE COPY of the hook
     script and wired through `gate_tool_status()` exactly like every real
     gate, is picked up automatically -- the failing-first case PR #872 never
     had a safety net for;
  3. a gate that fails OPEN on a missing script (no `gate_tool_status()` call)
     is correctly left OUT of the stub list, since stubbing it is unnecessary
     and would hide the fail-open path going untested.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import gate_stub_list  # noqa: E402

HOOK_SCRIPT = os.path.join(REPO_ROOT, "hooks", "pre-push-policy.sh")

# The fail-closed gates on origin/main as of this writing (every check_*
# function whose script resolves via gate_tool_status()). This is a pinned
# expectation, not a re-derivation -- if a new fail-closed gate lands without
# updating this list, this test is the signal to update it (the TTY fixture
# itself never needs touching, which is the whole point of deriving its list).
KNOWN_FAIL_CLOSED_GATES = sorted([
    "tracker_guard",
    "import_resolution_check",
    "claudemd_sync_gate",
    "gen_tool_index",
    "metrics_gate",
    "verify_test_suite_count",
    "encoding_lint",
    "verify_test_coverage",
    "conflict_marker_check",
    "linux_shape_check",
    "generated_push_gate",
])

DUMMY_FAIL_CLOSED_GATE = """
check_zzz() {
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local zzz_script="$aesop_root/tools/zzz_check.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$zzz_script")
  if [ "$tool_status" = "skip" ]; then
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "zzz_check.py" "$zzz_script"
    return 1
  fi
  return 0
}
"""

# A fail-OPEN-shaped gate (no gate_tool_status call at all, like the real
# check_claudemd_headroom / check_generated_paths) must NOT be derived, since
# a missing script there is a designed no-op, not a push-blocking condition.
DUMMY_FAIL_OPEN_GATE = """
check_yyy() {
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local yyy_script="$aesop_root/tools/yyy_check.py"
  if [ ! -f "$yyy_script" ]; then
    return 0
  fi
  return 0
}
"""


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestDerivationMatchesRealHook(unittest.TestCase):
    def test_derived_list_matches_known_fail_closed_gates(self):
        text = _read(HOOK_SCRIPT)
        derived = gate_stub_list.derive_stub_scripts(text)
        self.assertEqual(derived, KNOWN_FAIL_CLOSED_GATES)

    def test_cli_against_real_hook_script(self):
        rc = gate_stub_list.main([gate_stub_list.__file__, HOOK_SCRIPT])
        self.assertEqual(rc, 0)


class TestRedFirstNewGatePickedUp(unittest.TestCase):
    """The PR #872 class of escape: a brand-new gate must join the derived
    list automatically, with no hand edit to the test fixture or this tool."""

    def test_dummy_check_zzz_is_derived(self):
        text = _read(HOOK_SCRIPT)
        # Insert right before check_secret_scan, mirroring a real addition.
        marker = "check_secret_scan() {"
        self.assertIn(marker, text)
        mutated = text.replace(marker, DUMMY_FAIL_CLOSED_GATE + "\n" + marker, 1)

        derived = gate_stub_list.derive_stub_scripts(mutated)

        self.assertIn("zzz_check", derived)
        # Every real gate must still be present alongside the new one.
        for name in KNOWN_FAIL_CLOSED_GATES:
            self.assertIn(name, derived)

    def test_fail_open_shaped_gate_is_not_derived(self):
        text = _read(HOOK_SCRIPT)
        marker = "check_secret_scan() {"
        mutated = text.replace(marker, DUMMY_FAIL_OPEN_GATE + "\n" + marker, 1)

        derived = gate_stub_list.derive_stub_scripts(mutated)

        self.assertNotIn("yyy_check", derived)


class TestUnitHelpers(unittest.TestCase):
    def test_iter_check_function_bodies_splits_on_column_zero_brace(self):
        text = (
            "check_a() {\n  echo one\n}\n"
            "not_a_check() {\n  echo skip\n}\n"
            "check_b() {\n  echo two\n}\n"
        )
        names = [name for name, _body in gate_stub_list.iter_check_function_bodies(text)]
        self.assertEqual(names, ["check_a", "check_b"])

    def test_var_not_passed_to_gate_tool_status_is_ignored(self):
        text = (
            "check_c() {\n"
            '  local aesop_root\n'
            '  aesop_root=$(resolve_aesop_root)\n'
            '  local c_script="$aesop_root/tools/c_check.py"\n'
            "  # never calls gate_tool_status\n"
            "}\n"
        )
        self.assertEqual(gate_stub_list.derive_stub_scripts(text), [])

    def test_empty_source_yields_empty_list(self):
        self.assertEqual(gate_stub_list.derive_stub_scripts(""), [])

    def test_main_errors_on_unreadable_path(self):
        rc = gate_stub_list.main(
            [gate_stub_list.__file__, os.path.join(REPO_ROOT, "no-such-hook.sh")]
        )
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
