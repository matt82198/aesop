#!/usr/bin/env python3
"""Tests for tools/refuted_ledger.py and the committed docs/refuted.jsonl.

Proves the refuted-ledger tool:
1. Accepts a well-formed ledger and the committed seed file.
2. Rejects each schema violation with the offending line number (exit 1).
3. Rejects personal home paths, Windows user paths and UUIDs.
4. Computes stats arithmetic (counts, refute rate) exactly.
5. Redacts raw logs into committable ones (round trip validates).
6. Appends via `add` only after validation, leaving the file untouched on failure.

Fixtures are assembled at runtime so this source file contains no personal path.
Tests live in unittest.TestCase classes (repo gate: no bare test functions).
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import refuted_ledger as rl  # noqa: E402

COMMITTED = REPO_ROOT / "docs" / "refuted.jsonl"
TOOL = REPO_ROOT / "tools" / "refuted_ledger.py"

# Runtime-assembled hostile fixtures (no literal personal path in source).
DRIVE = "C" + ":"
WIN_HOME = DRIVE + "\\" + "Users" + "\\" + "fixtureuser"
WIN_HOME_JSON = WIN_HOME.replace("\\", "\\\\")
POSIX_HOME = "/" + "home" + "/" + "fixtureuser"
ENV_HOME = "$" + "HOME" + "/work"
UUID = "3f2a9c1e-" + "7b4d-" + "4e8f-" + "9a1b-" + "0c2d3e4f5a6b"


def _entry(**overrides):
    base = {
        "id": "R-0001",
        "date": "2026-10-15",
        "proposed_by": "orchestrator",
        "proposal": "Raise the test timeout to clear one flaky run.",
        "measured_by": "precedent",
        "verdict": "REFUTED",
        "evidence": "PR #123",
        "note": "Closed: budgets are not fitted to green.",
    }
    base.update(overrides)
    return base


def _write(path: Path, objs):
    lines = []
    for obj in objs:
        lines.append(obj if isinstance(obj, str) else json.dumps(obj))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _TmpCase(unittest.TestCase):
    """Gives each test a private temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *argv):
        """Run the CLI in-process, returning (rc, stdout+stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rl.main(list(argv))
        return rc, out.getvalue() + err.getvalue()

    def _validate(self, path: Path):
        return self._run("validate", str(path))


class TestValidInput(_TmpCase):
    def test_valid_file_passes(self):
        p = _write(
            self.tmp / "ledger.jsonl",
            [_entry(), _entry(id="R-0002", verdict="DEFERRED", measured_by="review")],
        )
        rc, text = self._validate(p)
        self.assertEqual(rc, 0, text)

    def test_committed_seed_ledger_passes(self):
        self.assertTrue(COMMITTED.exists(), "docs/refuted.jsonl must be committed")
        rc, text = self._validate(COMMITTED)
        self.assertEqual(rc, 0, text)

    def test_note_is_optional(self):
        obj = _entry()
        del obj["note"]
        rc, text = self._validate(_write(self.tmp / "l.jsonl", [obj]))
        self.assertEqual(rc, 0, text)

    def test_empty_file_is_valid_zero_entries(self):
        p = self.tmp / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        rc, text = self._validate(p)
        self.assertEqual(rc, 0, text)


class TestSchemaRejections(_TmpCase):
    BAD_OVERRIDES = [
        {"id": "R-1"},
        {"id": "r-0001"},
        {"id": "R-00001"},
        {"date": "2026-02-30"},
        {"date": "15-10-2026"},
        {"proposed_by": "robot"},
        {"measured_by": "vibes"},
        {"verdict": "MAYBE"},
        {"verdict": "refuted"},
        {"proposal": ""},
        {"proposal": 7},
        {"evidence": ""},
        {"evidence": "/var/tmp/x.log"},
        {"evidence": "~/notes/x.md"},
        {"evidence": DRIVE + "/Build/x.log"},
    ]

    def test_schema_violations_rejected_with_line_number(self):
        for overrides in self.BAD_OVERRIDES:
            with self.subTest(overrides=overrides):
                good = _entry(id="R-0002")
                bad = _entry(**overrides)
                p = _write(self.tmp / "l.jsonl", [good, bad])
                rc, text = self._validate(p)
                self.assertEqual(rc, 1, text)
                self.assertIn("line 2", text)

    def test_missing_required_key_rejected(self):
        obj = _entry()
        del obj["measured_by"]
        rc, text = self._validate(_write(self.tmp / "l.jsonl", [obj]))
        self.assertEqual(rc, 1)
        self.assertIn("line 1", text)
        self.assertIn("measured_by", text)

    def test_unknown_key_rejected(self):
        rc, text = self._validate(_write(self.tmp / "l.jsonl", [_entry(session="x")]))
        self.assertEqual(rc, 1)
        self.assertIn("line 1", text)

    def test_invalid_json_rejected_with_line_number(self):
        p = _write(self.tmp / "l.jsonl", [_entry(), "{not json"])
        rc, text = self._validate(p)
        self.assertEqual(rc, 1)
        self.assertIn("line 2", text)

    def test_duplicate_id_rejected(self):
        p = _write(self.tmp / "l.jsonl", [_entry(), _entry(proposal="other idea")])
        rc, text = self._validate(p)
        self.assertEqual(rc, 1)
        self.assertIn("line 2", text)
        self.assertIn("duplicate", text)


class TestPersonalPathRejections(_TmpCase):
    CASES = [
        ("evidence", WIN_HOME + "\\Desktop\\aesop\\run.log"),
        ("note", "see " + WIN_HOME + "\\notes.md"),
        ("proposal", "copied from " + WIN_HOME_JSON + "\\x"),
        ("evidence", POSIX_HOME + "/run.log"),
        ("note", "reads " + ENV_HOME),
        ("evidence", "session " + UUID),
        ("note", "transcript " + UUID),
    ]

    def test_personal_path_or_uuid_rejected(self):
        for field, value in self.CASES:
            with self.subTest(field=field):
                rc, text = self._validate(
                    _write(self.tmp / "l.jsonl", [_entry(**{field: value})])
                )
                self.assertEqual(rc, 1, text)
                self.assertIn("line 1", text)

    def test_windows_user_path_regex_matches_both_slash_styles(self):
        self.assertTrue(rl.WINDOWS_USER_PATH_RE.search(WIN_HOME))
        self.assertTrue(rl.WINDOWS_USER_PATH_RE.search(DRIVE + "/" + "Users" + "/" + "fixtureuser/x"))
        self.assertIsNone(rl.WINDOWS_USER_PATH_RE.search("docs" + "/" + "refuted.jsonl"))


class TestStats(_TmpCase):
    def _fixture(self):
        return _write(
            self.tmp / "s.jsonl",
            [
                _entry(id="R-0001", verdict="REFUTED", proposed_by="lane", measured_by="test"),
                _entry(id="R-0002", verdict="FALSE_POSITIVE", proposed_by="hook", measured_by="gate"),
                _entry(id="R-0003", verdict="DEFERRED", proposed_by="orchestrator", measured_by="review"),
                _entry(id="R-0004", verdict="DONE_BY_DESIGN", proposed_by="lane", measured_by="precedent"),
                _entry(id="R-0005", verdict="DEFERRED", proposed_by="user", measured_by="precedent"),
            ],
        )

    def test_stats_arithmetic(self):
        result = rl.compute_stats(self._fixture())
        self.assertEqual(result["total"], 5)
        self.assertEqual(
            result["by_verdict"],
            {"REFUTED": 1, "DEFERRED": 2, "DONE_BY_DESIGN": 1, "FALSE_POSITIVE": 1},
        )
        self.assertEqual(result["by_proposed_by"]["lane"], 2)
        self.assertEqual(result["by_proposed_by"]["hook"], 1)
        self.assertEqual(result["by_proposed_by"]["user"], 1)
        self.assertEqual(result["by_proposed_by"]["orchestrator"], 1)
        self.assertEqual(result["by_measured_by"]["precedent"], 2)
        self.assertEqual(result["by_measured_by"]["test"], 1)
        # refute rate = (REFUTED + FALSE_POSITIVE) / total = 2 / 5
        self.assertAlmostEqual(result["refute_rate"], 0.4)

    def test_empty_ledger_has_null_rate(self):
        result = rl.compute_stats(_write(self.tmp / "e.jsonl", []))
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["refute_rate"])

    def test_stats_json_cli(self):
        rc, out = self._run("stats", str(self._fixture()), "--json")
        self.assertEqual(rc, 0, out)
        parsed = json.loads(out)
        self.assertEqual(parsed["total"], 5)
        self.assertAlmostEqual(parsed["refute_rate"], 0.4)

    def test_stats_refuses_invalid_file(self):
        p = _write(self.tmp / "bad.jsonl", [_entry(verdict="MAYBE")])
        rc, _ = self._run("stats", str(p), "--json")
        self.assertEqual(rc, 1)


class TestRedact(_TmpCase):
    def test_redact_round_trip(self):
        raw = _write(
            self.tmp / "raw.jsonl",
            [
                _entry(note="log at " + WIN_HOME + "\\Desktop\\run.log"),
                _entry(id="R-0002", note="session " + UUID, evidence="PR #7"),
                _entry(id="R-0003", note="reads " + POSIX_HOME + "/x.log", evidence="PR #8"),
            ],
        )
        rc_raw, _ = self._validate(raw)
        self.assertEqual(rc_raw, 1, "raw log must fail validation")

        out = self.tmp / "clean.jsonl"
        rc, text = self._run("redact", str(raw), str(out))
        self.assertEqual(rc, 0, text)
        clean = out.read_text(encoding="utf-8")
        self.assertNotIn("fixtureuser", clean)
        self.assertNotIn(UUID, clean)
        self.assertIn("<home>", clean)
        self.assertIn("<session>", clean)

        rc_clean, text = self._validate(out)
        self.assertEqual(rc_clean, 0, text)

    def test_redact_is_idempotent(self):
        once = rl.redact_text(WIN_HOME_JSON + "\\\\x " + UUID)
        self.assertEqual(rl.redact_text(once), once)

    def test_redact_handles_json_escaped_backslashes(self):
        text = json.dumps({"note": WIN_HOME + "\\run.log"})
        cleaned = rl.redact_text(text)
        self.assertNotIn("fixtureuser", cleaned)
        json.loads(cleaned)  # still valid JSON


class TestAdd(_TmpCase):
    def test_add_appends_valid_line(self):
        p = _write(self.tmp / "l.jsonl", [_entry()])
        rc, text = self._run(
            "add", str(p),
            "--proposed-by", "lane",
            "--proposal", "Cache the lint result between shards.",
            "--measured-by", "ab",
            "--verdict", "REFUTED",
            "--evidence", "PR #999",
            "--date", "2026-10-15",
        )
        self.assertEqual(rc, 0, text)
        lines = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        added = json.loads(lines[1])
        self.assertEqual(added["id"], "R-0002")
        self.assertEqual(added["evidence"], "PR #999")
        rc2, text2 = self._validate(p)
        self.assertEqual(rc2, 0, text2)

    def test_add_rejects_invalid_and_leaves_file_untouched(self):
        p = _write(self.tmp / "l.jsonl", [_entry()])
        before = p.read_bytes()
        rc, _ = self._run(
            "add", str(p),
            "--proposed-by", "lane",
            "--proposal", "Idea.",
            "--measured-by", "ab",
            "--verdict", "REFUTED",
            "--evidence", WIN_HOME + "\\run.log",
            "--date", "2026-10-15",
        )
        self.assertEqual(rc, 1)
        self.assertEqual(p.read_bytes(), before)


class TestCliEntryPoint(_TmpCase):
    def test_subprocess_exit_codes(self):
        good = _write(self.tmp / "g.jsonl", [_entry()])
        bad = _write(self.tmp / "b.jsonl", [_entry(verdict="MAYBE")])
        ok = subprocess.run(
            [sys.executable, str(TOOL), "validate", str(good)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        fail = subprocess.run(
            [sys.executable, str(TOOL), "validate", str(bad)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(fail.returncode, 1)
        self.assertIn("line 1", fail.stderr)

    def test_missing_file_fails_closed(self):
        rc, _ = self._validate(self.tmp / "absent.jsonl")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
