#!/usr/bin/env python3
"""Regenerating merge driver (tools/generated_merge.py) contract tests.

The problem this driver kills (PRs #784, #856, #739 on 2026-10-06): `tools/INDEX.md`
merged with git's built-in `union` driver, which ends the conflict cascade but keeps
BOTH sides' lines verbatim -- so every merge-from-main that touched the index left
it unsorted or duplicated, `gen_tool_index.py --check` failed ci(0) with "generated
drift", and the lane had to notice and regenerate by hand.

Three layers are proved here, failing-first against fixture git repos:

  1. The git integration -- with `.gitattributes: tools/INDEX.md merge=aesop-regen`
     and the driver registered, two branches that each add a tool merge to an
     index that is byte-identical to a fresh regeneration (deduped, sorted), so
     `gen_tool_index.py --check` passes on the merged tree with no human step.
     The legacy `merge=union` attribute is the negative control: same merge, same
     clean exit, stale index.
  2. The driver's own contract -- structured three-way merge of index ENTRIES
     keyed by tool name (added/deleted/changed on one side wins; both-changed
     takes ours and reports it), rendered through the registered generator; a
     `.needs-regen` stamp (per-worktree, under `git rev-parse --git-path`) is
     written whenever the working tree's sources do not yet agree with the
     merged entries, because git merge-ort runs merge drivers BEFORE it writes
     the merged sources to disk, so a regenerate-from-tree would see ours only.
     An unregistered path is refused (exit 1 = git's ordinary conflict), and a
     missing %P is a usage error (exit 2).
  3. The registry contract -- every `generated_paths.REGISTRY` entry with a
     regenerator agrees with `merge_queue.REGENERATORS`, so the queue's batch
     repair and the driver can never disagree about who rebuilds an artifact.

Fixtures are real git repos in a tempdir; nothing touches the developer's cwd or
global git config.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
DRIVER = TOOLS / "generated_merge.py"
GENERATOR = TOOLS / "gen_tool_index.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.gen_tool_index as gen_tool_index  # noqa: E402
import tools.generated_paths as generated_paths  # noqa: E402
import tools.generated_merge as generated_merge  # noqa: E402

FIXTURE_TOOLS = ("gen_tool_index.py", "generated_paths.py", "generated_merge.py")


def git(repo, *args):
    return subprocess.run(  # subprocess-ok
        ["git"] + list(args), cwd=str(repo), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120)


def py(repo, *args):
    return subprocess.run(  # subprocess-ok
        [sys.executable] + list(args), cwd=str(repo), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120)


def add_tool(repo, name, purpose):
    # Stage it: the generator indexes `git ls-files tools/`, exactly as a lane
    # does (add the tool, then regenerate), so an unstaged tool is invisible.
    (repo / "tools" / name).write_text(
        '"""%s.\nINDEX: %s\n"""\n' % (name, purpose), encoding="utf-8", newline="\n")
    git(repo, "add", "--", "tools/%s" % name)


def regenerate(repo):
    proc = py(repo, str(repo / "tools" / "gen_tool_index.py"), "--regenerate")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def check_index(repo):
    return py(repo, str(repo / "tools" / "gen_tool_index.py"), "--check")


def stamp_path(repo):
    proc = git(repo, "rev-parse", "--git-path", generated_merge.STAMP_NAME)
    assert proc.returncode == 0, proc.stderr
    raw = proc.stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


class FixtureRepoMixin:
    """A repo whose tools/ holds real copies of the generator, registry and driver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        for name in FIXTURE_TOOLS:
            shutil.copy(TOOLS / name, self.repo / "tools" / name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "core.autocrlf", "false")
        git(self.repo, "add", "-A")  # the generator indexes tracked files only

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, attribute, register_driver):
        (self.repo / ".gitattributes").write_text(
            "tools/INDEX.md merge=%s\n" % attribute, encoding="utf-8", newline="\n")
        if register_driver:
            git(self.repo, "config", "merge.aesop-regen.name", "regenerate generated artifacts")
            git(self.repo, "config", "merge.aesop-regen.driver",
                '"%s" "%s" %%O %%A %%B %%L %%P' % (
                    Path(sys.executable).as_posix(),
                    (self.repo / "tools" / "generated_merge.py").as_posix()))
        add_tool(self.repo, "alpha.py", "alpha purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

        git(self.repo, "checkout", "-q", "-b", "lane-a")
        add_tool(self.repo, "beta.py", "beta purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "lane a adds beta")

        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", "-b", "lane-b")
        add_tool(self.repo, "gamma.py", "gamma purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "lane b adds gamma")
        self.assertEqual(check_index(self.repo).returncode, 0, "each lane is green alone")


class TestGitIntegration(FixtureRepoMixin, unittest.TestCase):
    """Two branches each add a tool line; the merge must not need a human regenerate."""

    def test_registered_driver_merge_is_already_regenerated(self):
        self.seed("aesop-regen", register_driver=True)
        merge = git(self.repo, "merge", "--no-edit", "lane-a")
        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        self.assertNotIn("UU", git(self.repo, "status", "--porcelain").stdout)
        body = (self.repo / "tools" / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(body.count("`beta.py`"), 1, body)
        self.assertEqual(body.count("`gamma.py`"), 1, body)
        lines = [l for l in body.splitlines() if l.strip()]
        self.assertEqual(len(lines), len(set(lines)), "no duplicated lines: %s" % body)
        # THE property: the merged tree passes the byte-identity gate untouched.
        proc = check_index(self.repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_legacy_union_attribute_leaves_the_index_stale(self):
        # Negative control: this is the 2026-10-06 failure shape. Same merge,
        # same clean exit, but the union keeps both appended lines in ours-then-
        # theirs order and the gate fails until someone regenerates.
        self.seed("union", register_driver=False)
        merge = git(self.repo, "merge", "--no-edit", "lane-a")
        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        proc = check_index(self.repo)
        self.assertEqual(proc.returncode, 1, "union must reproduce the drift")
        self.assertIn("run: python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md",
                      proc.stderr)

    def test_unregistered_clone_degrades_to_an_ordinary_conflict(self):
        # Registration is per clone (git never reads driver commands out of the
        # repo). An unregistered clone must get a visible conflict, never a
        # silent wrong merge.
        self.seed("aesop-regen", register_driver=False)
        merge = git(self.repo, "merge", "--no-edit", "lane-a")
        self.assertNotEqual(merge.returncode, 0)
        self.assertIn("UU tools/INDEX.md", git(self.repo, "status", "--porcelain").stdout)
        git(self.repo, "merge", "--abort")


class TestDriverContract(FixtureRepoMixin, unittest.TestCase):
    """Drive the driver directly with ancestor/ours/theirs temp files."""

    def seed_tree(self, *tools):
        for name, purpose in tools:
            add_tool(self.repo, name, purpose)
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "seed")

    def render(self, entries):
        return generated_merge.render_index(sorted(entries))

    def write_sides(self, base, ours, theirs):
        o = self.tmp / "ancestor"
        a = self.tmp / "ours"
        b = self.tmp / "theirs"
        o.write_text(self.render(base), encoding="utf-8", newline="\n")
        a.write_text(self.render(ours), encoding="utf-8", newline="\n")
        b.write_text(self.render(theirs), encoding="utf-8", newline="\n")
        return o, a, b

    def run_driver(self, *args):
        return py(self.repo, str(self.repo / "tools" / "generated_merge.py"), *args)

    def test_stamps_when_the_tree_does_not_yet_hold_theirs_sources(self):
        # merge-ort calls the driver before writing merged sources: the tree has
        # alpha + beta (ours) only, theirs adds gamma. The result must still
        # carry gamma, and the stamp must be written so the push gate verifies.
        self.seed_tree(("alpha.py", "alpha purpose"), ("beta.py", "beta purpose"))
        fixture = [(n, "%s purpose" % n[:-3]) for n in FIXTURE_TOOLS]
        base = fixture + [("alpha.py", "alpha purpose")]
        ours = base + [("beta.py", "beta purpose")]
        theirs = base + [("gamma.py", "gamma purpose")]
        o, a, b = self.write_sides(base, ours, theirs)
        proc = self.run_driver(str(o), str(a), str(b), "7", "tools/INDEX.md")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        merged = a.read_text(encoding="utf-8")
        self.assertIn("- `beta.py` -- beta purpose", merged)
        self.assertIn("- `gamma.py` -- gamma purpose", merged)
        self.assertEqual(merged, self.render(ours + [("gamma.py", "gamma purpose")]))
        stamp = stamp_path(self.repo)
        self.assertTrue(stamp.exists(), "stamp must be written when the tree disagrees")
        self.assertIn("tools/INDEX.md", stamp.read_text(encoding="utf-8"))

    def test_no_stamp_when_the_tree_already_agrees(self):
        # If the working tree does hold every merged source (git wrote them
        # first, or the merge touched no source), the rendered result IS the
        # regeneration and no stamp is needed.
        self.seed_tree(("alpha.py", "alpha purpose"), ("beta.py", "beta purpose"),
                       ("gamma.py", "gamma purpose"))
        # The three sides describe the tree's REAL entries (the fixture tools'
        # own INDEX: lines included): base lacks beta and gamma, each side adds one.
        tree = dict(gen_tool_index.collect(self.repo)[0])
        base = [(n, d) for n, d in tree.items() if n not in ("beta.py", "gamma.py")]
        ours = base + [("beta.py", tree["beta.py"])]
        theirs = base + [("gamma.py", tree["gamma.py"])]
        o, a, b = self.write_sides(base, ours, theirs)
        proc = self.run_driver(str(o), str(a), str(b), "tools/INDEX.md")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(a.read_text(encoding="utf-8"), self.render(tree.items()))
        self.assertEqual(check_index(self.repo).returncode, 0)
        self.assertFalse(stamp_path(self.repo).exists())

    def test_three_way_entry_semantics(self):
        base = {"a": "A", "b": "B", "c": "C", "d": "D"}
        ours = {"a": "A", "b": "B2", "d": "D", "e": "E"}          # changed b, deleted c, added e
        theirs = {"a": "A", "b": "B", "c": "C", "d": "D3", "f": "F"}  # changed d, added f
        merged, conflicts = generated_merge.merge_entries(base, ours, theirs)
        self.assertEqual(merged, {"a": "A", "b": "B2", "d": "D3", "e": "E", "f": "F"})
        self.assertEqual(conflicts, [])

    def test_both_sides_changing_one_entry_takes_ours_and_reports(self):
        base = {"a": "A"}
        merged, conflicts = generated_merge.merge_entries(base, {"a": "ours"}, {"a": "theirs"})
        self.assertEqual(merged, {"a": "ours"})
        self.assertEqual(conflicts, ["a"])

    def test_unregistered_path_is_refused_so_git_conflicts_normally(self):
        self.seed_tree(("alpha.py", "alpha purpose"))
        o, a, b = self.write_sides([("x.py", "x")], [("x.py", "y")], [("x.py", "z")])
        before = a.read_bytes()
        proc = self.run_driver(str(o), str(a), str(b), "README.md")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(a.read_bytes(), before, "ours must be left untouched on refusal")

    def test_missing_path_placeholder_is_a_usage_error(self):
        o, a, b = self.write_sides([], [], [])
        proc = self.run_driver(str(o), str(a), str(b))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class TestInstallMergeDrivers(FixtureRepoMixin, unittest.TestCase):
    """tools/install_merge_drivers.py: per-clone, idempotent, --check is read-only."""

    INSTALLER = TOOLS / "install_merge_drivers.py"

    def installer(self, *args):
        return py(self.repo, str(self.INSTALLER), *args)

    def driver_cmd(self, key):
        return git(self.repo, "config", "--get", "merge.%s.driver" % key).stdout.strip()

    def test_check_reports_gaps_without_writing_then_install_is_idempotent(self):
        proc = self.installer("--check")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("merge.aesop-regen.driver", proc.stderr)
        self.assertIn("run: python tools/install_merge_drivers.py", proc.stderr)
        self.assertEqual(self.driver_cmd("aesop-regen"), "", "--check must not write")

        proc = self.installer()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.driver_cmd("aesop-regen"),
                         "python tools/generated_merge.py %O %A %B %L %P")
        self.assertEqual(self.driver_cmd("aesop-json-union"),
                         "python tools/json_list_merge.py %O %A %B")
        self.assertEqual(self.installer("--check").returncode, 0)
        self.assertEqual(self.installer("--quiet").returncode, 0)
        self.assertEqual(self.driver_cmd("aesop-regen"),
                         "python tools/generated_merge.py %O %A %B %L %P")

    def test_python_override_and_non_repo_root(self):
        proc = self.installer("--python", "py3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.driver_cmd("aesop-regen"), "py3 tools/generated_merge.py %O %A %B %L %P")
        proc = self.installer("--root", str(self.tmp / "not-a-repo"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class TestRegistryContract(unittest.TestCase):
    def test_index_md_declares_its_regenerator(self):
        argv = generated_paths.regenerator_for("tools/INDEX.md")
        self.assertEqual(argv, ["tools/gen_tool_index.py", "--regenerate"])
        self.assertIsNone(generated_paths.regenerator_for("state/ledger/x.jsonl"))
        self.assertIsNone(generated_paths.regenerator_for("README.md"))

    def test_regenerators_agree_with_merge_queue(self):
        import tools.merge_queue as merge_queue
        registry = {tuple(generated_paths.regenerator_for(p))
                    for p in generated_paths.regenerable_paths()}
        self.assertEqual(set(merge_queue.REGENERATORS), registry)

    def test_gitattributes_routes_index_through_the_regen_driver(self):
        text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("tools/INDEX.md merge=aesop-regen", text)
        self.assertNotIn("tools/INDEX.md merge=union", text)


if __name__ == "__main__":
    unittest.main()
