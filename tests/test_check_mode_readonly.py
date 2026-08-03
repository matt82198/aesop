#!/usr/bin/env python3
"""
Meta-gate: every tools/*.py that exposes a `--check` flag must be READ-ONLY.

CONTRACT
--------
`--check` is this repo's universal "verify, do not mutate" verb: CI gates,
pre-push hooks and the orchestrator's idle_tick all invoke `<tool> --check`
expecting a verdict and an exit code, never a tree mutation. A `--check` that
silently repairs what it is supposed to report turns a red gate green by
rewriting the evidence -- the drift is never surfaced, and a developer's working
tree is modified out from under them by a command that reads as an assertion.

This gate enforces that contract mechanically:

  1. ENUMERATE  -- run `--help` on every tools/*.py and keep the ones whose help
                   text advertises a bare `--check` flag. Enumeration is from the
                   tool's own `--help` (same subprocess pattern as
                   tests/test_cli_help_hygiene.py), not a source grep, so a tool
                   that gains `--check` is covered the moment it ships. A flag
                   like `--check-alerts` (tools/cost_projection.py) is NOT a
                   `--check` flag and is correctly excluded by the word-boundary
                   match.
  2. SNAPSHOT   -- sha256 every file in a throwaway fixture tree.
  3. RUN        -- invoke `<fixture>/tools/<tool>.py --check` with cwd at the
                   fixture root.
  4. COMPARE    -- the post-run snapshot must be byte-identical. Any added,
                   removed or modified file fails, and the diagnostic names the
                   exact paths.

HERMETIC / SAFE BY CONSTRUCTION
-------------------------------
The tool is executed from a COPY of tools/ inside the fixture, never from the
real worktree. This matters: several tools resolve their target root from
`__file__` rather than cwd, so running the live script would let a write-happy
`--check` mutate the developer's actual repo -- the very bug this gate hunts.
Copying tools/ into the fixture makes both cwd-relative and `__file__`-relative
resolution land inside the disposable tree. `AESOP_STATE_ROOT` / `AESOP_ROOT`
are likewise pointed at the fixture, so state writes are caught rather than
leaked into the live project.

FIXTURES ARE MINIMAL AND PER-TOOL
---------------------------------
The shared skeleton is a small plausible aesop-shaped tree (tools/ copy plus a
handful of marker files), not a clone of the repo -- the whole sweep stays in
the seconds range. Tools whose write path only fires on a specific input get a
targeted fixture, documented inline in TOOL_FIXTURES below; a generic fixture
that never triggers the repair branch would make this gate vacuously green.

SNAPSHOT EXCLUSIONS (deliberate, not loopholes)
----------------------------------------------
  .git/        -- `git ls-files` (used by several gates) legitimately refreshes
                  the index; that is git's own bookkeeping, not a tree write.
  __pycache__/ -- interpreter bytecode. Also suppressed via
                  PYTHONDONTWRITEBYTECODE=1; excluded belt-and-braces.

KNOWN OFFENDERS
---------------
Tools listed in KNOWN_OFFENDERS are wired with `unittest.expectedFailure`, never
skipped. Semantics, and the reason it is expectedFailure rather than a skip:
  - offender still writes  -> "expected failure", suite stays green.
  - offender stops writing -> "unexpected success", which unittest counts as a
    FAILED run. That is the intended trip-wire: the moment the fix lands, CI
    goes red until the entry is deleted from KNOWN_OFFENDERS, flipping the tool
    from tolerated to enforced. A skip would rot silently forever.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"

# Directories excluded from the byte-identity snapshot (see module docstring).
SNAPSHOT_EXCLUDED_DIRS = {".git", "__pycache__", "node_modules"}

# A bare `--check`, not `--check-alerts` / `--check_foo`.
CHECK_FLAG_RE = re.compile(r"--check(?![-\w])")

HELP_TIMEOUT_S = 60
CHECK_TIMEOUT_S = 120


# ---------------------------------------------------------------------------
# Known offenders -- expectedFailure, with a reference. Never a silent skip.
# ---------------------------------------------------------------------------
# Delete an entry the moment its fix merges; the resulting "unexpected success"
# is the designed signal that the tool is now enforceable.
KNOWN_OFFENDERS = {
    # verify_test_suite_count.py was a known offender until guard/count-check-no-write
    # merged into main. Its --check/--strict modes are now strictly read-only, so the
    # entry is deleted (per the rule above) and the tool is enforced like any other.
    "cost_ceiling.py": (
        "--check reaches read_ledger_total_tokens -> fleet_ledger.parse_ledger_rows() "
        "-> ensure_ledger_header(), which mkdirs and writes "
        "<state>/ledger/OUTCOMES-LEDGER.md on a fresh tree. Discovered by this "
        "gate (GAP6); needs its own fix lane -- the header write belongs on the "
        "append path, not the read path."
    ),
}


# ---------------------------------------------------------------------------
# Per-tool fixture specs
# ---------------------------------------------------------------------------
class FixtureSpec:
    """Extra CLI args plus a fixture mutation that exercises a tool's write path.

    `why` documents what the targeted fixture buys over the shared skeleton.
    """

    def __init__(self, args=(), mutate=None, why="shared skeleton is sufficient"):
        self.args = tuple(args)
        self.mutate = mutate
        self.why = why


def _drift_suite_counts(root: Path):
    """Counts deliberately wrong so the auto-correct branch fires."""
    (root / "tests" / "CLAUDE.md").write_text(
        "# tests/\n\n**Shell (999 suites)**: x\n\n"
        "**Node (999 suites)**: x\n\n**Python (999 suites)**: x\n",
        encoding="utf-8",
    )


def _drift_stats(root: Path):
    """stats.json + README markers stale, so --regenerate/--update-readme would write."""
    (root / "stats.json").write_text(
        '{"generated":"1970-01-01T00:00:00Z","commits":1,"tests":1}\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        "# fx\n\n<!-- SELF-STATS:START -->\nstale block\n<!-- SELF-STATS:END -->\n",
        encoding="utf-8",
    )


def _oversized_log(root: Path):
    """A log well past the threshold, so --check must report rotation without rotating."""
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "fleet.log").write_text(
        "".join("line %d\n" % i for i in range(200)), encoding="utf-8"
    )


TOOL_FIXTURES = {
    "verify_test_suite_count.py": FixtureSpec(
        mutate=_drift_suite_counts,
        why="counts must DRIFT or the auto-correct write path never runs and the "
            "check passes vacuously",
    ),
    "self_stats.py": FixtureSpec(
        mutate=_drift_stats,
        why="stats.json/README must be stale so --check is tempted down the "
            "--regenerate / --update-readme write path",
    ),
    "rotate_logs.py": FixtureSpec(
        args=("logs/fleet.log", "--max-lines", "5"),
        mutate=_oversized_log,
        why="takes a positional logfile; rotation is its whole purpose, so --check "
            "on an oversized log is the sharpest possible read-only probe "
            "(must exit 3 'rotation needed' and archive nothing)",
    ),
    "commit_lint.py": FixtureSpec(
        args=("--message", "bad subject line with no type"),
        mutate=None,
        why="needs --message or --range; without one it exits 2 before linting",
    ),
    "claudemd_sync_gate.py": FixtureSpec(
        args=("--base-ref", "HEAD"),
        mutate=None,
        why="drift is computed against a base ref; the fixture has exactly one commit",
    ),
}

DEFAULT_FIXTURE = FixtureSpec()


# ---------------------------------------------------------------------------
# Enumeration (from --help, like tests/test_cli_help_hygiene.py)
# ---------------------------------------------------------------------------
def _help_advertises_check(script: Path):
    env = os.environ.copy()
    env.pop("AESOP_STATE_ROOT", None)
    env.pop("AESOP_ROOT", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=HELP_TIMEOUT_S, env=env,
            cwd=str(REPO_ROOT), encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None
    blob = (result.stdout or "") + (result.stderr or "")
    return script.name if CHECK_FLAG_RE.search(blob) else None


def discover_check_tools():
    """Every tools/*.py whose own --help advertises a bare `--check` flag."""
    scripts = [p for p in sorted(TOOLS_DIR.glob("*.py")) if not p.name.startswith("__")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(_help_advertises_check, scripts))
    return [name for name in found if name]


CHECK_TOOLS = discover_check_tools()


# ---------------------------------------------------------------------------
# Fixture construction + snapshotting
# ---------------------------------------------------------------------------
_TEMPLATE_ROOT = None
_TEMPLATE_DIR = None


def _build_template(dest: Path):
    """Minimal aesop-shaped tree: a tools/ copy plus the marker files gates read."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TOOLS_DIR, dest / "tools")
    (dest / "tests").mkdir()
    (dest / "docs").mkdir()
    (dest / "state").mkdir()
    (dest / ".github" / "workflows").mkdir(parents=True)

    (dest / "tests" / "test_sample.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_a(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    (dest / "tests" / "sample.test.mjs").write_text("// sample\n", encoding="utf-8")
    (dest / "tests" / "sample.test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (dest / "tests" / "CLAUDE.md").write_text(
        "# tests/\n\n**Shell (1 suites)**: x\n\n**Node (1 suites)**: x\n\n"
        "**Python (1 suites)**: x\n",
        encoding="utf-8",
    )
    (dest / "CLAUDE.md").write_text(
        "# Fixture\n\n## Domain map\n\n- **tools/** - build utilities\n", encoding="utf-8"
    )
    (dest / "STATE.md").write_text(
        "# STATE\n\n## NEXT STEPS\n\n1. **Sample item** (PR #1 merged).\n", encoding="utf-8"
    )
    (dest / "BUILDLOG.md").write_text("# BUILDLOG\n", encoding="utf-8")
    (dest / "README.md").write_text("# fx\n", encoding="utf-8")
    (dest / "package.json").write_text(
        '{"name":"fx","version":"0.0.0","scripts":{}}\n', encoding="utf-8"
    )
    (dest / "tracker.json").write_text('{"items":[]}\n', encoding="utf-8")
    (dest / "aesop.config.json").write_text('{"project":{"name":"fx"}}\n', encoding="utf-8")
    (dest / "stats.json").write_text('{"generated":"1970-01-01T00:00:00Z"}\n', encoding="utf-8")
    (dest / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\non: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo hi\n",
        encoding="utf-8",
    )

    _init_fixture_repo(dest)


def _init_fixture_repo(dest: Path):
    """Make `dest` (a throwaway tempfile-derived tree) a real local git repo.

    Several gates shell out to `git ls-files` / `git diff`, so the fixture needs
    real history. Identity is set with `--local` and every command is scoped by
    `cwd=dest`: the live repo's config is never touched.
    """
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "--local", "user.email", "fixture@example.com"],
        ["git", "config", "--local", "user.name", "Fixture"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "feat: fixture baseline"],
    ):
        subprocess.run(cmd, cwd=str(dest), capture_output=True, timeout=60)


def setUpModule():
    global _TEMPLATE_ROOT, _TEMPLATE_DIR
    _TEMPLATE_ROOT = Path(tempfile.mkdtemp(prefix="aesop-check-readonly-"))
    _TEMPLATE_DIR = _TEMPLATE_ROOT / "template"
    _build_template(_TEMPLATE_DIR)


def tearDownModule():
    if _TEMPLATE_ROOT is not None:
        shutil.rmtree(_TEMPLATE_ROOT, ignore_errors=True)


def snapshot_tree(root: Path):
    """sha256 of every file under root, keyed by posix relpath."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SNAPSHOT_EXCLUDED_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            rel = path.relative_to(root).as_posix()
            try:
                snap[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                snap[rel] = "<unreadable>"
    return snap


def describe_mutations(before, after):
    """Human-readable list of tree mutations; empty list means byte-identical."""
    lines = []
    for rel in sorted(set(after) - set(before)):
        lines.append("CREATED  " + rel)
    for rel in sorted(set(before) - set(after)):
        lines.append("DELETED  " + rel)
    for rel in sorted(set(before) & set(after)):
        if before[rel] != after[rel]:
            lines.append("MODIFIED " + rel)
    return lines


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
class TestCheckModeIsReadOnly(unittest.TestCase):
    """`<tool> --check` must leave the tree byte-identical."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-check-fx-"))
        self.fixture = self.fixture_root / "repo"
        shutil.copytree(_TEMPLATE_DIR, self.fixture)

    def tearDown(self):
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def assert_check_is_readonly(self, tool_name):
        spec = TOOL_FIXTURES.get(tool_name, DEFAULT_FIXTURE)
        if spec.mutate is not None:
            spec.mutate(self.fixture)

        before = snapshot_tree(self.fixture)

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Point state resolution INSIDE the fixture so state writes are caught
        # here rather than leaking into the developer's real project.
        env["AESOP_STATE_ROOT"] = str(self.fixture / "state")
        env["AESOP_ROOT"] = str(self.fixture)

        cmd = [sys.executable, str(self.fixture / "tools" / tool_name), "--check"]
        cmd.extend(spec.args)
        try:
            subprocess.run(
                cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S,
                cwd=str(self.fixture), env=env, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            self.fail("tools/%s --check timed out after %ss" % (tool_name, CHECK_TIMEOUT_S))

        # Exit code is deliberately NOT asserted: a gate legitimately reports a
        # violation (non-zero) against a minimal fixture. The invariant under
        # test is byte-identity of the tree, whatever the verdict.
        after = snapshot_tree(self.fixture)
        mutations = describe_mutations(before, after)
        self.assertEqual(
            [], mutations,
            "tools/%s --check MUTATED the tree (fixture: %s).\n"
            "--check must verify, never repair. Move the write behind an "
            "explicit --fix/--regenerate flag.\n  %s"
            % (tool_name, spec.why, "\n  ".join(mutations)),
        )

    def test_enumeration_is_non_trivial(self):
        """Guard the gate itself: a broken enumeration must not read as green."""
        self.assertGreaterEqual(
            len(CHECK_TOOLS), 20,
            "expected many tools/*.py to expose --check; enumeration returned %d "
            "(%s). A silently-empty sweep is a vacuous gate."
            % (len(CHECK_TOOLS), CHECK_TOOLS),
        )
        for name in KNOWN_OFFENDERS:
            self.assertIn(
                name, CHECK_TOOLS,
                "KNOWN_OFFENDERS lists %s but enumeration did not find it; the "
                "entry is stale (tool renamed or --check removed) -- delete it."
                % name,
            )


def _attach_per_tool_tests():
    """One test method per discovered tool, so the report carries per-tool verdicts."""
    for tool_name in CHECK_TOOLS:
        stem = tool_name[:-3] if tool_name.endswith(".py") else tool_name

        def make(name):
            def test(self):
                self.assert_check_is_readonly(name)
            return test

        attr = "test_%s_check_is_readonly" % stem
        method = make(tool_name)
        method.__name__ = attr
        if tool_name in KNOWN_OFFENDERS:
            method.__doc__ = "KNOWN OFFENDER (xfail): %s" % KNOWN_OFFENDERS[tool_name]
            method = unittest.expectedFailure(method)
        else:
            method.__doc__ = "tools/%s --check must not mutate the tree." % tool_name
        setattr(TestCheckModeIsReadOnly, attr, method)


_attach_per_tool_tests()


if __name__ == "__main__":
    unittest.main()
