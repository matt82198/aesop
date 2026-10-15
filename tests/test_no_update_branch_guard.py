#!/usr/bin/env python3
"""Repo-wide guard: no tracked tool, script, hook, workflow or skill may call the
server-side branch update (the `update-branch` REST endpoint / `gh pr update-branch`).

Why: a GitHub-authored update-branch merge commit is created server-side. No box ever
ran the gates on that tree, so no signed receipt can honestly exist for it. Signing it
after the fact would be a fitted green. Lanes merge origin/main LOCALLY instead
(merge_queue.py / REGENERATORS flow: merge, regenerate, explicit-refspec push).

The guard drives the real `git ls-files` view of the repo, so an untracked scratch file
does not count and a tracked one always does. It fails with the offending path:line.
"""

import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tokens that name the banned server-side update. Matched case-sensitively.
BANNED_TOKENS = ("update-branch", "updateBranch")

# Top-level prefixes whose tracked files must never contain a banned token.
SCANNED_PREFIXES = ("tools/", "scripts/", "hooks/", ".github/", "skills/")

# Explicit allowlist: (repo-relative path) -> reason. Only the guard itself and docs
# that EXPLAIN the ban may name the token. Docs outside SCANNED_PREFIXES are not
# scanned at all, so this set is deliberately small.
ALLOWLIST = {
    "tests/test_no_update_branch_guard.py": "this guard names the banned tokens",
}

# Exact explanatory phrases that may carry a banned token, per file. The verify-receipt
# MISSING line must name the cause verbatim (the operator greps for it), so that one
# sentence is exempt in that one file. Any other occurrence in that file still fails.
ALLOWED_PHRASES = {
    "tools/verify_receipt.py": (
        "server-authored update-branch merge; no box ran the gates; merge main locally and push",
    ),
}


def _tracked_files():
    env = dict(os.environ)
    res = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
                         capture_output=True, encoding="utf-8", errors="replace",
                         timeout=60, env=env)
    if res.returncode != 0:
        raise RuntimeError("git ls-files failed: %s" % (res.stderr or "").strip()[:300])
    return [p for p in (res.stdout or "").split("\0") if p]


def find_banned_references(paths, root=REPO_ROOT):
    """Return sorted [(path, lineno, token, line)] for every banned reference in the
    scanned tracked files that is not allowlisted."""
    hits = []
    for rel in paths:
        if not rel.startswith(SCANNED_PREFIXES):
            continue
        if rel in ALLOWLIST:
            continue
        full = Path(root) / rel
        if not full.is_file():
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        phrases = ALLOWED_PHRASES.get(rel, ())
        for lineno, line in enumerate(text.splitlines(), start=1):
            scanned = line
            for phrase in phrases:
                scanned = scanned.replace(phrase, "")
            for token in BANNED_TOKENS:
                if token in scanned:
                    hits.append((rel, lineno, token, line.strip()[:160]))
    return sorted(hits)


class TestNoUpdateBranchGuard(unittest.TestCase):
    def test_no_tracked_tool_script_hook_workflow_or_skill_names_update_branch(self):
        hits = find_banned_references(_tracked_files())
        report = "\n".join("%s:%d [%s] %s" % h for h in hits)
        self.assertEqual(hits, [], "banned server-side update-branch reference(s):\n" + report)

    def test_guard_detects_a_planted_reference(self):
        # Control: the scanner itself must find a planted token, or the test above is vacuous.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            (root / "tools" / "planted.py").write_text(
                'subprocess.run(["gh", "pr", "%s", "7"])\n' % BANNED_TOKENS[0], encoding="utf-8")
            hits = find_banned_references(["tools/planted.py"], root=root)
            self.assertEqual(len(hits), 1, hits)
            self.assertEqual(hits[0][:3], ("tools/planted.py", 1, BANNED_TOKENS[0]))

    def test_allowed_phrase_exemption_does_not_open_the_whole_file(self):
        # Control: the exempt cause sentence passes in verify_receipt.py, but a bare banned
        # token in the same file must still be reported.
        import tempfile
        exempt = ALLOWED_PHRASES["tools/verify_receipt.py"][0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            (root / "tools" / "verify_receipt.py").write_text(
                'CAUSE = "%s"\nGH = ["gh", "pr", "%s"]\n' % (exempt, BANNED_TOKENS[0]), encoding="utf-8")
            hits = find_banned_references(["tools/verify_receipt.py"], root=root)
            self.assertEqual([h[1] for h in hits], [2], hits)

    def test_out_of_scope_paths_are_not_scanned(self):
        self.assertEqual(find_banned_references(["docs/ANYTHING.md"], root=REPO_ROOT), [])


if __name__ == "__main__":
    unittest.main()
