#!/usr/bin/env python3
"""Typed JSON list-union merge driver (tools/json_list_merge.py) contract tests.

Covers the driver semantics the contended-file fix depends on: sorted+deduped
union, the ancestor-deletion rule, byte-format preservation, the %O %A %B git
signature writing into %A, and fail-closed exit 1 on every malformed or
unsupported input so git falls back to a conventional conflict.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "json_list_merge.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.json_list_merge as json_list_merge  # noqa: E402


class MergeDriverTestCase(unittest.TestCase):
    """Shared fixture: a temp dir holding the three sides as real files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def write_json(self, name, obj, trailing_newline=False):
        text = json.dumps(obj, indent=2) + ("\n" if trailing_newline else "")
        return self.write(name, text)

    def run_cli(self, args, env=None):
        """Invoke the tool as a subprocess exactly as git would."""
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(  # subprocess-ok
            [sys.executable, str(TOOL)] + [str(a) for a in args],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=run_env,
        )

    def merge_files(self, ancestor, ours, theirs):
        """Run the driver over three lists; return (exit code, parsed ours)."""
        o = self.write_json("ancestor.json", ancestor)
        a = self.write_json("ours.json", ours)
        b = self.write_json("theirs.json", theirs)
        result = self.run_cli([o, a, b])
        payload = None
        try:
            payload = json.loads(a.read_text(encoding="utf-8"))
        except ValueError:
            pass
        return result.returncode, payload, result


class TestUnionSemantics(MergeDriverTestCase):

    def test_union_is_sorted_and_deduplicated(self):
        rc, out, res = self.merge_files(
            ancestor=["a"],
            ours=["a", "c", "b"],
            theirs=["b", "d", "a"],
        )
        self.assertEqual(rc, 0, res.stderr)
        self.assertEqual(out, ["a", "b", "c", "d"])

    def test_disjoint_lane_appends_both_survive(self):
        # The exact contended-file scenario: two lanes each append one entry.
        rc, out, res = self.merge_files(
            ancestor=["base1", "base2"],
            ours=["base1", "base2", "lane-q4"],
            theirs=["base1", "base2", "lane-q1"],
        )
        self.assertEqual(rc, 0, res.stderr)
        self.assertEqual(out, ["base1", "base2", "lane-q1", "lane-q4"])

    def test_identical_sides_are_idempotent(self):
        rc, out, res = self.merge_files(["x"], ["x", "y"], ["x", "y"])
        self.assertEqual(rc, 0, res.stderr)
        self.assertEqual(out, ["x", "y"])

    def test_empty_lists_merge_to_empty(self):
        rc, out, res = self.merge_files([], [], [])
        self.assertEqual(rc, 0, res.stderr)
        self.assertEqual(out, [])


class TestAncestorDeletionSemantics(MergeDriverTestCase):

    def test_deleted_by_both_sides_stays_deleted(self):
        rc, out, res = self.merge_files(
            ancestor=["keep", "gone"],
            ours=["keep"],
            theirs=["keep"],
        )
        self.assertEqual(rc, 0, res.stderr)
        self.assertNotIn("gone", out)
        self.assertEqual(out, ["keep"])

    def test_deleted_by_one_side_only_stays_kept(self):
        rc, out, res = self.merge_files(
            ancestor=["keep", "contested"],
            ours=["keep"],
            theirs=["keep", "contested"],
        )
        self.assertEqual(rc, 0, res.stderr)
        self.assertIn("contested", out)

    def test_deletion_and_addition_in_the_same_merge(self):
        rc, out, res = self.merge_files(
            ancestor=["a", "b", "c"],
            ours=["a", "b", "new-ours"],       # dropped c
            theirs=["a", "c", "new-theirs"],   # dropped b, kept c
        )
        self.assertEqual(rc, 0, res.stderr)
        # b: dropped by theirs only -> kept. c: dropped by ours only -> kept.
        self.assertEqual(out, ["a", "b", "c", "new-ours", "new-theirs"])

    def test_entry_absent_from_ancestor_and_both_sides_never_appears(self):
        rc, out, res = self.merge_files(["a"], ["a"], ["a"])
        self.assertEqual(rc, 0, res.stderr)
        self.assertEqual(out, ["a"])


class TestBaselineObjectShape(MergeDriverTestCase):
    """The real .stateapi-baseline.json shape: {"violations": [...]}."""

    def test_object_with_one_string_array_merges(self):
        o = self.write_json("o.json", {"violations": ["m.py@p1"]})
        a = self.write_json("a.json", {"violations": ["m.py@p1", "m.py@p2"]})
        b = self.write_json("b.json", {"violations": ["m.py@p1", "m.py@p3"]})
        res = self.run_cli([o, a, b])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            json.loads(a.read_text(encoding="utf-8")),
            {"violations": ["m.py@p1", "m.py@p2", "m.py@p3"]},
        )

    def test_scalar_sibling_keys_are_preserved(self):
        payload = {"_comment": "regenerate via --update-baseline", "violations": ["x"]}
        o = self.write_json("o.json", payload)
        a = self.write_json("a.json", payload)
        b = self.write_json("b.json", {"_comment": payload["_comment"], "violations": ["y"]})
        res = self.run_cli([o, a, b])
        self.assertEqual(res.returncode, 0, res.stderr)
        merged = json.loads(a.read_text(encoding="utf-8"))
        self.assertEqual(merged["_comment"], payload["_comment"])
        self.assertEqual(merged["violations"], ["x", "y"])
        self.assertEqual(list(merged.keys()), ["_comment", "violations"])

    def test_count_map_baseline_is_refused(self):
        # .portability-baseline.json / .subprocess-guard-baseline.json shape:
        # union is not a sound merge for counts, so the driver must fail closed.
        payload = {"violations": {"a.py@X": 2}}
        o = self.write_json("o.json", payload)
        a = self.write_json("a.json", payload)
        b = self.write_json("b.json", {"violations": {"a.py@X": 3}})
        original = a.read_text(encoding="utf-8")
        res = self.run_cli([o, a, b])
        self.assertEqual(res.returncode, 1)
        self.assertEqual(a.read_text(encoding="utf-8"), original)

    def test_real_stateapi_baseline_shape_is_supported(self):
        baseline = REPO_ROOT / ".stateapi-baseline.json"
        if not baseline.exists():
            self.skipTest(".stateapi-baseline.json absent")
        kind, key, items, _ = json_list_merge.parse_shape(
            baseline.read_text(encoding="utf-8"), "live")
        self.assertEqual(kind, "object")
        self.assertEqual(key, "violations")
        self.assertTrue(all(isinstance(i, str) for i in items))


class TestFailClosed(MergeDriverTestCase):

    def assert_refused(self, ancestor_text, ours_text, theirs_text):
        o = self.write("o.json", ancestor_text)
        a = self.write("a.json", ours_text)
        b = self.write("b.json", theirs_text)
        before = a.read_bytes()
        res = self.run_cli([o, a, b])
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(a.read_bytes(), before, "ours (%A) must be left untouched")

    def test_malformed_json_ours_exits_1(self):
        self.assert_refused('["a"]', '["a", ', '["b"]')

    def test_malformed_json_theirs_exits_1(self):
        self.assert_refused('["a"]', '["a"]', 'not json at all')

    def test_malformed_json_ancestor_exits_1(self):
        self.assert_refused('{{{', '["a"]', '["b"]')

    def test_non_list_top_level_exits_1(self):
        self.assert_refused('"a string"', '"a string"', '"another"')

    def test_list_of_non_strings_exits_1(self):
        self.assert_refused('[1, 2]', '[1, 2, 3]', '[1, 2, 4]')

    def test_shape_mismatch_between_sides_exits_1(self):
        self.assert_refused('["a"]', '["a"]', '{"violations": ["a"]}')

    def test_object_with_two_string_arrays_exits_1(self):
        payload = '{"one": ["a"], "two": ["b"]}'
        self.assert_refused(payload, payload, payload)

    def test_object_with_no_string_array_exits_1(self):
        payload = '{"_comment": "hi"}'
        self.assert_refused(payload, payload, payload)

    def test_list_key_mismatch_exits_1(self):
        self.assert_refused(
            '{"violations": ["a"]}', '{"violations": ["a"]}', '{"findings": ["a"]}')

    def test_missing_file_exits_1(self):
        a = self.write_json("a.json", ["x"])
        res = self.run_cli([self.tmp / "does-not-exist.json", a, a])
        self.assertEqual(res.returncode, 1)

    def test_too_few_arguments_exits_2(self):
        res = self.run_cli(["only-one.json"])
        self.assertEqual(res.returncode, 2)

    def test_unknown_flag_exits_2(self):
        a = self.write_json("a.json", ["x"])
        res = self.run_cli([a, a, a, "--wipe-everything"])
        self.assertEqual(res.returncode, 2)


class TestByteFormat(MergeDriverTestCase):

    def test_two_space_indent_matches_generator_convention(self):
        o = self.write_json("o.json", {"violations": []})
        a = self.write_json("a.json", {"violations": ["b"]})
        b = self.write_json("b.json", {"violations": ["a"]})
        res = self.run_cli([o, a, b])
        self.assertEqual(res.returncode, 0, res.stderr)
        text = a.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps({"violations": ["a", "b"]}, indent=2))

    def test_trailing_newline_habit_of_ours_is_preserved(self):
        o = self.write_json("o.json", ["a"], trailing_newline=True)
        a = self.write_json("a.json", ["a"], trailing_newline=True)
        b = self.write_json("b.json", ["b"], trailing_newline=True)
        self.assertEqual(self.run_cli([o, a, b]).returncode, 0)
        self.assertTrue(a.read_text(encoding="utf-8").endswith("\n"))

    def test_no_trailing_newline_habit_of_ours_is_preserved(self):
        o = self.write_json("o.json", ["a"])
        a = self.write_json("a.json", ["a"])
        b = self.write_json("b.json", ["b"])
        self.assertEqual(self.run_cli([o, a, b]).returncode, 0)
        self.assertFalse(a.read_text(encoding="utf-8").endswith("\n"))

    def test_bom_prefixed_side_is_tolerated(self):
        self.write("o.json", "\ufeff" + json.dumps(["a"]))
        self.write("a.json", "\ufeff" + json.dumps(["a"]))
        self.write("b.json", "\ufeff" + json.dumps(["b"]))
        res = self.run_cli([self.tmp / "o.json", self.tmp / "a.json", self.tmp / "b.json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads((self.tmp / "a.json").read_text(encoding="utf-8")),
                         ["a", "b"])

    def test_stdout_mode_does_not_write_ours(self):
        o = self.write_json("o.json", ["a"])
        a = self.write_json("a.json", ["a"])
        b = self.write_json("b.json", ["b"])
        before = a.read_bytes()
        res = self.run_cli([o, a, b, "--stdout"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), ["a", "b"])
        self.assertEqual(a.read_bytes(), before)

    def test_extra_positional_placeholders_are_ignored(self):
        # Git's five-placeholder form: %O %A %B %L %P
        o = self.write_json("o.json", ["a"])
        a = self.write_json("a.json", ["a"])
        b = self.write_json("b.json", ["b"])
        res = self.run_cli([o, a, b, "7", ".stateapi-baseline.json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(a.read_text(encoding="utf-8")), ["a", "b"])


class TestGitDriverIntegration(MergeDriverTestCase):
    """Prove the driver actually resolves a real git merge conflict."""

    def git(self, *args, cwd=None):
        return subprocess.run(  # subprocess-ok
            ["git"] + list(args),
            cwd=str(cwd or self.repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

    def _seed_repo(self, register_driver):
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        (self.repo / ".gitattributes").write_text(
            "*-baseline.json merge=aesop-json-union\n", encoding="utf-8", newline="\n")
        if register_driver:
            self.git("config", "merge.aesop-json-union.name", "union-and-sort")
            self.git("config", "merge.aesop-json-union.driver",
                     '"%s" "%s" %%O %%A %%B' % (sys.executable, TOOL))
        target = self.repo / ".stateapi-baseline.json"
        target.write_text(json.dumps({"violations": ["base"]}, indent=2),
                          encoding="utf-8", newline="\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

        self.git("checkout", "-q", "-b", "lane-a")
        target.write_text(json.dumps({"violations": ["base", "lane-a"]}, indent=2),
                          encoding="utf-8", newline="\n")
        self.git("commit", "-q", "-am", "lane a appends")

        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "lane-b")
        target.write_text(json.dumps({"violations": ["base", "lane-b"]}, indent=2),
                          encoding="utf-8", newline="\n")
        self.git("commit", "-q", "-am", "lane b appends")
        return target

    def test_registered_driver_resolves_the_conflict(self):
        target = self._seed_repo(register_driver=True)
        merge = self.git("merge", "--no-edit", "lane-a")
        self.assertEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8")),
            {"violations": ["base", "lane-a", "lane-b"]},
        )
        status = self.git("status", "--porcelain")
        self.assertNotIn("UU", status.stdout)

    def test_unregistered_clone_still_gets_an_ordinary_conflict(self):
        # Registration is per-clone; an unregistered clone must degrade to
        # today's behavior, never to a silent wrong merge.
        self._seed_repo(register_driver=False)
        merge = self.git("merge", "--no-edit", "lane-a")
        self.assertNotEqual(merge.returncode, 0)


if __name__ == "__main__":
    unittest.main()
