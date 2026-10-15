"""Tests for .github/CODEOWNERS -- the enforcement-surface owner map.

Behavioural, not text-grep: the CODEOWNERS parser and the gate-owner check are
driven against the real repo (every pattern must match a tracked file; every
gate tool enumerated by tools/gate_inventory.GATE_GLOBS must have an owner) and
against a throwaway git index built in a tempdir (a new gate tool with no owner
line must be reported). Nothing touches the developer's cwd or global git config.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODEOWNERS_PATH = os.path.join(REPO_ROOT, ".github", "CODEOWNERS")

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import gate_inventory  # noqa: E402


def parse_codeowners(text):
    """Return [(pattern, [owners...]), ...] for every non-comment, non-blank line."""
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rules.append((parts[0], parts[1:]))
    return rules


def tracked_matches(root, pattern):
    """Tracked paths under root that the pattern selects (git pathspec semantics)."""
    proc = subprocess.run(
        ["git", "ls-files", "--", pattern],
        cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("git ls-files failed for %r: %s" % (pattern, proc.stderr))
    return [p for p in proc.stdout.splitlines() if p]


def gate_tools(root):
    """Every gate-shaped tool, enumerated exactly as tools/gate_inventory.py does."""
    found = set()
    for glob in gate_inventory.GATE_GLOBS:
        found.update(tracked_matches(root, glob))
    return sorted(found)


def unowned_gates(root, codeowners_text):
    """Gate tools that no CODEOWNERS pattern selects."""
    owned = set()
    for pattern, _owners in parse_codeowners(codeowners_text):
        owned.update(tracked_matches(root, pattern))
    return [g for g in gate_tools(root) if g not in owned]


def read_codeowners():
    with open(CODEOWNERS_PATH, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


class CodeownersParseTests(unittest.TestCase):
    def test_every_rule_has_at_least_one_owner(self):
        for pattern, owners in parse_codeowners(read_codeowners()):
            self.assertTrue(owners, "pattern %r has no owner" % pattern)
            for owner in owners:
                self.assertTrue(owner.startswith("@"),
                                "owner %r on %r is not an @handle" % (owner, pattern))

    def test_repo_owner_is_the_enforcement_owner(self):
        rules = parse_codeowners(read_codeowners())
        self.assertTrue(rules, "CODEOWNERS parsed to zero rules")
        for pattern, owners in rules:
            self.assertEqual(owners, ["@matt82198"],
                             "pattern %r must be owned by @matt82198" % pattern)

    def test_parser_ignores_comments_and_blanks(self):
        text = "# comment\n\n   \nhooks/ @matt82198\n"
        self.assertEqual(parse_codeowners(text), [("hooks/", ["@matt82198"])])


class CodeownersPatternMatchTests(unittest.TestCase):
    def test_every_pattern_matches_a_tracked_file(self):
        dead = []
        for pattern, _owners in parse_codeowners(read_codeowners()):
            if not tracked_matches(REPO_ROOT, pattern):
                dead.append(pattern)
        self.assertEqual(dead, [], "CODEOWNERS patterns matching no tracked file")

    def test_gate_inventory_enumerates_gate_tools(self):
        # Guards the premise of the owner check: an empty enumeration would
        # make the ownership test vacuously green.
        self.assertGreater(len(gate_tools(REPO_ROOT)), 20)


class GateOwnershipTests(unittest.TestCase):
    def test_every_gate_tool_has_an_owner_line(self):
        missing = unowned_gates(REPO_ROOT, read_codeowners())
        self.assertEqual(missing, [],
                         "gate tools with no CODEOWNERS owner line: %r" % missing)

    def test_new_unowned_gate_is_reported(self):
        # Behavioural control: a fixture repo gains a new gate tool that no
        # owner line covers, and the check must name it.
        root = tempfile.mkdtemp(prefix="codeowners-fixture-")
        try:
            os.makedirs(os.path.join(root, "tools"))
            for name in ("owned_gate.py", "orphan_lint.py"):
                with open(os.path.join(root, "tools", name), "w", encoding="utf-8") as fh:
                    fh.write("# fixture\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                           capture_output=True, timeout=60)
            subprocess.run(["git", "add", "tools"], cwd=root, check=True,
                           capture_output=True, timeout=60)
            owners = "tools/owned_gate.py @matt82198\n"
            self.assertEqual(unowned_gates(root, owners), ["tools/orphan_lint.py"])
            fixed = owners + "tools/orphan_lint.py @matt82198\n"
            self.assertEqual(unowned_gates(root, fixed), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
