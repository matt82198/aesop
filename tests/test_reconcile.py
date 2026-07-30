"""Tests for tools/reconcile.py — STATE.md (git) <-> state_store (SQLite) drift.

Hermetic: every test works against a tempdir STATE.md + tempdir sqlite db.
Never touches the real repo's STATE.md or a real state_store db.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.store import EventStore  # noqa: E402
from tools.reconcile import (  # noqa: E402
    decide_resolution,
    detect_drift,
    main,
    read_git_phase,
    read_store_phase,
    resolve_drift,
    write_store_phase,
)


def _write_state_md(path: Path, phase: str | None) -> None:
    if phase is None:
        content = "# STATE\n\n## Intent\nSomething.\n"
    else:
        content = (
            "# STATE\n\n## Intent\nSomething.\n\n"
            f"## Phase: `{phase}` (2026-07-17, current)\n\n"
            "## NEXT STEPS\n- do the thing\n"
        )
    path.write_text(content, encoding="utf-8")


class HermeticFixtureMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_md = os.path.join(self.tmp, "STATE.md")
        self.db = os.path.join(self.tmp, "events.db")
        self._stores = []  # track EventStore instances for tearDown cleanup

    def _make_store(self):
        """Create an EventStore and track it for tearDown cleanup."""
        store = EventStore(self.db)
        self._stores.append(store)
        return store

    def tearDown(self):
        import shutil
        for s in self._stores:
            try:
                s.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReadGitPhaseTest(HermeticFixtureMixin, unittest.TestCase):
    def test_parses_phase_from_heading(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        self.assertEqual(read_git_phase(self.state_md), "wave-28-reconcile")

    def test_missing_heading_returns_none(self):
        _write_state_md(Path(self.state_md), None)
        self.assertIsNone(read_git_phase(self.state_md))

    def test_missing_file_returns_none(self):
        self.assertIsNone(read_git_phase(os.path.join(self.tmp, "nope.md")))


class ReadStorePhaseTest(HermeticFixtureMixin, unittest.TestCase):
    def test_missing_db_returns_none_and_creates_nothing(self):
        # Report-mode reads must never create a db file as a side effect.
        self.assertIsNone(read_store_phase(self.db))
        self.assertFalse(os.path.exists(self.db))

    def test_empty_meta_stream_returns_none(self):
        self._make_store()  # creates an empty db with tables, no events
        self.assertIsNone(read_store_phase(self.db))

    def test_returns_latest_phase_set_value(self):
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27"}, "t")
        store.append("meta", "phase_set", {"phase": "wave-28"}, "t")
        self.assertEqual(read_store_phase(self.db), "wave-28")

    def test_ignores_unrelated_event_types_in_same_stream(self):
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-28"}, "t")
        store.append("meta", "something_else", {"phase": "wave-99"}, "t")
        self.assertEqual(read_store_phase(self.db), "wave-28")


class DecideResolutionTest(unittest.TestCase):
    """Pure-function tests for the authority mechanism, including the
    state_store-authoritative direction that no current field exercises
    end-to-end (see reconcile.py's module docstring)."""

    def test_no_drift_when_equal(self):
        drift, target, value = decide_resolution("wave-28", "wave-28", "git")
        self.assertFalse(drift)
        self.assertIsNone(target)
        self.assertEqual(value, "wave-28")

    def test_git_authority_targets_store_on_drift(self):
        drift, target, value = decide_resolution("wave-28", "wave-27", "git")
        self.assertTrue(drift)
        self.assertEqual(target, "store")
        self.assertEqual(value, "wave-28")

    def test_store_authority_targets_git_on_drift(self):
        drift, target, value = decide_resolution("wave-27", "wave-28", "store")
        self.assertTrue(drift)
        self.assertEqual(target, "git")
        self.assertEqual(value, "wave-28")

    def test_none_vs_value_is_drift(self):
        drift, target, value = decide_resolution(None, "wave-28", "git")
        self.assertTrue(drift)
        self.assertEqual(target, "store")
        self.assertIsNone(value)

    def test_unknown_authority_raises(self):
        with self.assertRaises(ValueError):
            decide_resolution("a", "b", "nobody")


class DetectDriftTest(HermeticFixtureMixin, unittest.TestCase):
    def test_known_drift_is_detected(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")

        report = detect_drift(self.state_md, self.db)

        self.assertTrue(report["drift"])
        phase_entry = next(f for f in report["fields"] if f["field"] == "phase")
        self.assertTrue(phase_entry["drift"])
        self.assertEqual(phase_entry["git_value"], "wave-28-reconcile")
        self.assertEqual(phase_entry["store_value"], "wave-27-old")
        self.assertEqual(phase_entry["fix_target"], "store")

    def test_known_agreement_is_clean(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-28-reconcile"}, "t")

        report = detect_drift(self.state_md, self.db)

        self.assertFalse(report["drift"])
        phase_entry = report["fields"][0]
        self.assertFalse(phase_entry["drift"])
        self.assertIsNone(phase_entry["fix_target"])

    def test_never_creates_db_file(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        detect_drift(self.state_md, self.db)
        self.assertFalse(os.path.exists(self.db), "detect_drift must be read-only")

    def test_never_mutates_state_md(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        before = Path(self.state_md).read_text(encoding="utf-8")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")
        detect_drift(self.state_md, self.db)
        after = Path(self.state_md).read_text(encoding="utf-8")
        self.assertEqual(before, after)


class ResolveDriftTest(HermeticFixtureMixin, unittest.TestCase):
    def test_resolve_writes_authoritative_value_to_store(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")

        report = resolve_drift(self.state_md, self.db)

        phase_entry = report["fields"][0]
        self.assertEqual(phase_entry["action"], "resolved")
        self.assertEqual(read_store_phase(self.db), "wave-28-reconcile")
        self.assertFalse(report["drift"], "report should show no outstanding drift after a successful resolve")

    def test_resolve_never_writes_to_state_md(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")
        before = Path(self.state_md).read_text(encoding="utf-8")

        resolve_drift(self.state_md, self.db)

        after = Path(self.state_md).read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_resolve_on_clean_state_is_a_noop(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-28-reconcile"}, "t")

        events_before = len(store.read("meta"))
        report = resolve_drift(self.state_md, self.db)
        events_after = len(self._make_store().read("meta"))

        self.assertFalse(report["drift"])
        self.assertEqual(report["fields"][0]["action"], "noop")
        self.assertEqual(events_before, events_after, "a clean resolve must not append a redundant event")

    def test_resolve_is_idempotent_across_two_calls(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")

        resolve_drift(self.state_md, self.db)
        count_after_first = len(self._make_store().read("meta"))

        report_second = resolve_drift(self.state_md, self.db)
        count_after_second = len(self._make_store().read("meta"))

        self.assertEqual(report_second["fields"][0]["action"], "noop")
        self.assertEqual(count_after_first, count_after_second, "resolving an already-resolved field must not append again")

    def test_resolve_creates_db_only_when_drift_exists(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        self.assertFalse(os.path.exists(self.db))
        resolve_drift(self.state_md, self.db)
        self.assertTrue(os.path.exists(self.db), "resolving real drift (store had no phase at all) must persist it")
        self.assertEqual(read_store_phase(self.db), "wave-28-reconcile")


class CliTest(HermeticFixtureMixin, unittest.TestCase):
    def test_exit_0_when_no_drift(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-28-reconcile"}, "t")

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--state-md", self.state_md, "--db", self.db])
        self.assertEqual(code, 0)
        self.assertIn("no drift", buf.getvalue())

    def test_exit_1_when_drift_and_not_resolved(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--state-md", self.state_md, "--db", self.db])
        self.assertEqual(code, 1)
        self.assertIn("DRIFT DETECTED", buf.getvalue())
        # report-only mode must not have resolved anything
        self.assertEqual(read_store_phase(self.db), "wave-27-old")

    def test_exit_0_after_resolve_clears_drift(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        store = self._make_store()
        store.append("meta", "phase_set", {"phase": "wave-27-old"}, "t")

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--state-md", self.state_md, "--db", self.db, "--resolve"])
        self.assertEqual(code, 0)

    def test_exit_2_when_state_md_missing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--state-md", os.path.join(self.tmp, "nope.md"), "--db", self.db])
        self.assertEqual(code, 2)

    def test_json_output_is_valid_json(self):
        _write_state_md(Path(self.state_md), "wave-28-reconcile")
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--state-md", self.state_md, "--db", self.db, "--json"])
        parsed = json.loads(buf.getvalue())
        self.assertIn("drift", parsed)
        self.assertIn("fields", parsed)


class WriteStorePhaseTest(HermeticFixtureMixin, unittest.TestCase):
    def test_appends_versioned_event(self):
        v1 = write_store_phase(self.db, "wave-1")
        v2 = write_store_phase(self.db, "wave-2")
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(read_store_phase(self.db), "wave-2")


if __name__ == "__main__":
    unittest.main()
