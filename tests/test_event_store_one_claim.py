"""One-claim slice: KINDS allowlist, record-merge (facts from gh), verify_claim."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
PKG_PARENT = REPO / "tools"
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))

from event_store import cli as cli_mod  # noqa: E402
from event_store import claims  # noqa: E402
from event_store.store import Store  # noqa: E402

CLI = REPO / "tools" / "event_store" / "cli.py"
MERGE = "a" * 40
HEAD = "b" * 40
OTHER = "c" * 40
RP = "matt82198/aesop"


def make_store(root, project="aesop"):
    root = Path(root)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")
    return root


def count_events(store_path):
    n = 0
    for f in (Path(store_path) / "events").glob("*.jsonl"):
        n += len([x for x in f.read_text(encoding="utf-8").split("\n") if x])
    return n


def gh_json(state="MERGED", merge=MERGE, head=HEAD, merged_at="2026-11-14T10:00:00Z",
            base="main"):
    return json.dumps({"state": state,
                       "mergeCommit": {"oid": merge} if merge else None,
                       "headRefOid": head, "mergedAt": merged_at, "baseRefName": base})


class FakeGh:
    def __init__(self, out="", rc=0, exc=None):
        self.out, self.rc, self.exc, self.calls = out, rc, exc, []

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        if self.exc:
            raise self.exc
        return self.rc, self.out


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name
        self.root = make_store(Path(self.tmp) / "s-aesop")
        self.store = Store(self.root)
        reg = {"tool": str(REPO), "stores": {"aesop": {"path": str(self.root), "goals": ["G0"]}},
               "frozen": {"log": str(Path(self.tmp) / "frozen")}}
        self.reg = Path(self.tmp) / "event-stores.json"
        self.reg.write_text(json.dumps(reg), encoding="utf-8")

    def run_main(self, argv, gh=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"EVENT_STORES_REGISTRY": str(self.reg)}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli_mod.main(argv, gh_runner=gh)
        return rc, out.getvalue(), err.getvalue()

    def run_sub(self, *args):
        env = dict(os.environ)
        env["EVENT_STORES_REGISTRY"] = str(self.reg)
        return subprocess.run([sys.executable, "-I", str(CLI), *args], env=env,
                              capture_output=True, encoding="utf-8", errors="replace")

    def add_merge(self, pr=7, merge=MERGE, head=HEAD, repo=RP):
        gh = FakeGh(gh_json(merge=merge, head=head))
        return claims.record_merge(self.store, repo, pr, gh)


class KindsTests(Base):
    def test_unknown_kind_refused_nothing_written(self):
        r = self.run_sub("append", "--project", "aesop", "--kind", "bogus",
                         "--actor", "o", "--text", "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unknown kind", r.stderr)
        self.assertEqual(count_events(self.root), 0)

    def test_every_existing_kind_still_accepted(self):
        expected = {"verdict", "ruling", "dispatch", "handback", "pr_state", "checkpoint",
                    "release", "finding", "note", "playtest_start", "playtest_end",
                    "migrated_genesis", "migrated_close"}
        self.assertTrue(expected <= set(cli_mod.KINDS))
        for kind in sorted(expected):
            args = ["append", "--project", "aesop", "--kind", kind, "--actor", "o",
                    "--text", "t"]
            if kind == "ruling":
                args += ["--ref", "goal=G0"]
            r = self.run_sub(*args)
            self.assertEqual(r.returncode, 0, "%s: %s" % (kind, r.stderr))
        self.assertEqual(count_events(self.root), len(expected))

    def test_hand_typed_merge_refused(self):
        r = self.run_sub("append", "--project", "aesop", "--kind", "merge", "--actor", "o",
                         "--text", "merged")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(count_events(self.root), 0)


class RecordMergeTests(Base):
    def test_merged_appends_facts_from_gh_with_arglist_and_timeout(self):
        gh = FakeGh(gh_json())
        status, _ = claims.record_merge(self.store, RP, 7, gh)
        self.assertEqual(status, "recorded")
        argv, timeout = gh.calls[0]
        self.assertEqual(argv, ["gh", "pr", "view", "7", "--repo", RP, "--json",
                                "state,mergeCommit,headRefOid,mergedAt,baseRefName"])
        self.assertIsInstance(timeout, (int, float))
        self.assertGreater(timeout, 0)
        ev = self.store.load()[0]
        self.assertEqual(ev["kind"], "merge")
        self.assertEqual(ev["refs"]["repo"], RP)
        self.assertEqual(ev["refs"]["pr"], "7")
        self.assertEqual(ev["refs"]["merge_sha"], MERGE)
        self.assertEqual(ev["refs"]["head_sha"], HEAD)
        self.assertEqual(ev["refs"]["source"], "gh")
        self.assertEqual(self.store.verify(), [])

    def assert_failed(self, gh):
        with self.assertRaises(claims.RecordMergeError):
            claims.record_merge(self.store, RP, 7, gh)
        self.assertEqual(count_events(self.root), 0)

    def test_open_pr_writes_nothing(self):
        self.assert_failed(FakeGh(gh_json(state="OPEN", merge=None)))

    def test_open_pr_with_full_shas_still_writes_nothing(self):
        self.assert_failed(FakeGh(gh_json(state="OPEN", merge=MERGE, head=HEAD)))

    def test_closed_pr_writes_nothing(self):
        self.assert_failed(FakeGh(gh_json(state="CLOSED", merge=None)))

    def test_gh_nonzero_writes_nothing(self):
        self.assert_failed(FakeGh("", rc=1))

    def test_gh_timeout_writes_nothing(self):
        self.assert_failed(FakeGh(exc=subprocess.TimeoutExpired(["gh"], 1)))

    def test_gh_missing_binary_writes_nothing(self):
        self.assert_failed(FakeGh(exc=FileNotFoundError("gh")))

    def test_unparseable_output_writes_nothing(self):
        self.assert_failed(FakeGh("not json {"))

    def test_merged_without_merge_commit_writes_nothing(self):
        self.assert_failed(FakeGh(gh_json(merge=None)))

    def test_bad_sha_shape_writes_nothing(self):
        self.assert_failed(FakeGh(gh_json(merge="zzz")))

    def test_idempotent_second_record(self):
        gh = FakeGh(gh_json())
        self.assertEqual(claims.record_merge(self.store, RP, 7, gh)[0], "recorded")
        self.assertEqual(claims.record_merge(self.store, RP, 7, gh)[0], "already-recorded")
        self.assertEqual(count_events(self.root), 1)

    def test_conflicting_sha_for_same_pr_fails(self):
        self.add_merge(7, merge=MERGE)
        with self.assertRaises(claims.RecordMergeError):
            claims.record_merge(self.store, RP, 7, FakeGh(gh_json(merge=OTHER)))
        self.assertEqual(count_events(self.root), 1)

    def test_invalid_repo_or_pr_rejected_before_gh(self):
        gh = FakeGh(gh_json())
        for repo, pr in (("--repo", 7), ("a/b/c", 7), ("nope", 7), (RP, 0), (RP, -3), (RP, "x")):
            with self.assertRaises(claims.RecordMergeError):
                claims.record_merge(self.store, repo, pr, gh)
        self.assertEqual(gh.calls, [])

    def test_cli_failure_line_and_exit(self):
        rc, out, _ = self.run_main(["record-merge", RP, "7", "--project", "aesop"],
                                   gh=FakeGh(gh_json(state="OPEN", merge=None)))
        self.assertNotEqual(rc, 0)
        self.assertTrue(out.startswith("RECORD-MERGE FAILED:"), out)
        self.assertEqual(count_events(self.root), 0)

    def test_cli_success_then_already_recorded(self):
        gh = FakeGh(gh_json())
        rc, out, _ = self.run_main(["record-merge", RP, "7", "--project", "aesop"], gh=gh)
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.run_main(["record-merge", RP, "7", "--project", "aesop"], gh=gh)
        self.assertEqual(rc, 0)
        self.assertIn("already-recorded", out)
        self.assertEqual(count_events(self.root), 1)


class VerifyClaimTests(Base):
    def setUp(self):
        super().setUp()
        self.add_merge(7)

    def v(self, sha, pr=7, repo=RP):
        return claims.verify_claim(self.store, repo, pr, sha)

    def test_verified_by_merge_sha_with_seq(self):
        r = self.v(MERGE)
        self.assertEqual(r["status"], "verified")
        self.assertEqual(r["seq"], 1)

    def test_verified_by_head_sha(self):
        self.assertEqual(self.v(HEAD)["status"], "verified")

    def test_verified_by_prefix_of_seven(self):
        self.assertEqual(self.v(MERGE[:7])["status"], "verified")

    def test_short_prefix_is_unknown(self):
        self.assertEqual(self.v(MERGE[:6])["status"], "unknown")

    def test_non_hex_sha_is_unknown(self):
        self.assertEqual(self.v("zzzzzzzz")["status"], "unknown")

    def test_wrong_sha_refuted(self):
        r = self.v(OTHER)
        self.assertEqual(r["status"], "refuted")
        self.assertEqual(r["seq"], 1)

    def test_no_event_unknown(self):
        self.assertEqual(self.v(MERGE, pr=8)["status"], "unknown")
        self.assertEqual(self.v(MERGE, repo="matt82198/other")["status"], "unknown")

    def test_sha_in_text_but_not_fields_does_not_verify(self):
        self.store.append("note", "o", "PR 9 merged at " + OTHER, refs={"pr": "9"})
        self.assertEqual(self.v(OTHER, pr=9)["status"], "unknown")
        self.add_merge(9, merge="d" * 40, head="e" * 40)
        self.assertEqual(self.v(OTHER, pr=9)["status"], "refuted")

    def test_broken_chain_is_unknown_never_verified(self):
        self.store.append("note", "o", "after", refs={})
        p = next(iter(self.store.day_files()))
        raw = p.read_bytes()
        p.write_bytes(raw.replace(b" merged at ", b" MERGED at ", 1))
        self.assertNotEqual(p.read_bytes(), raw)
        r = self.v(MERGE)
        self.assertEqual(r["status"], "unknown")
        self.assertIn("chain", r["reason"])

    def test_cli_exit_codes(self):
        base = ["verify-claim", RP, "7"]
        rc, out, _ = self.run_main(base + [MERGE, "--project", "aesop"])
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.run_main(base + [OTHER, "--project", "aesop"])
        self.assertEqual(rc, 1, out)
        rc, out, _ = self.run_main(["verify-claim", RP, "99", MERGE, "--project", "aesop"])
        self.assertEqual(rc, 2, out)
        rc, out, _ = self.run_main(base + [MERGE, "--project", "nonexistent"])
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()
