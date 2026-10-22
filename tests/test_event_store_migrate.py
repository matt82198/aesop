"""Tests for tools/event_store/migrate.py: the one-time v1 -> per-project v2 split.

Every case drives the real CLI (subprocess, `python -I`) against temp dirs; the real
v1 store is never touched. The fixture tests/fixtures/event_store/source-v1.jsonl is
built by build_fixture_lines() below so its prev-chain shas are real.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATE = REPO / "tools" / "event_store" / "migrate.py"
FIXTURE = REPO / "tests" / "fixtures" / "event_store" / "source-v1.jsonl"
PROJECTS = ("aesop", "keyrun", "psinasty", "brain")

# (kind, text, refs, expected project, expected rule)
FIXTURE_ROWS = [
    ("finding", "plain text with no names", {"goal": "G2"}, "keyrun", "goal"),
    ("note", "keyrun is named here but repo ref wins", {"repo": "conductor3"}, "brain", "repo"),
    ("note", "nothing named", {"keyrun_pr_606": "1"}, "keyrun", "refkey"),
    ("note", "sleeper roster trade", {}, "psinasty", "text1"),
    ("checkpoint", "aesop and keyrun both moved", {}, "aesop", "multi"),
    ("note", "hello there", {}, "aesop", "default"),
    ("note", "keyrun named but goal G3 wins", {"goal": "G3"}, "psinasty", "goal"),
    ("note", "FROZEN: split into per-project stores", {"frozen": "1"}, "aesop", "default"),
]


def line_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_fixture_lines(rows=None):
    """Build a valid single-day v1 chain (insertion-ordered keys, prev = sha of prior line)."""
    lines = []
    prev = ""
    for index, row in enumerate(rows or FIXTURE_ROWS, start=1):
        kind, text, refs = row[0], row[1], row[2]
        obj = {"ts": f"2026-10-22T00:{index:02d}:00Z", "seq": index, "kind": kind,
               "actor": "orchestrator", "session": "s-1", "text": text, "refs": refs,
               "prev": prev}
        raw = dump(obj)
        lines.append(raw)
        prev = line_sha(raw)
    return lines


def write_source(root, lines):
    events = Path(root) / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "2026-10-22.jsonl").write_bytes(b"\n".join(lines) + b"\n")
    return Path(root)


def run_migrate(source, targets, *extra):
    cmd = [sys.executable, "-I", str(MIGRATE), "--source", str(source), "--targets"]
    cmd += [f"{name}={path}" for name, path in targets.items()]
    cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")


def read_store(target):
    path = Path(target) / "events" / "0000-genesis.jsonl"
    return [json.loads(x) for x in path.read_bytes().split(b"\n") if x]


class MigrateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="es-migrate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = write_source(self.tmp / "src", build_fixture_lines())
        self.targets = {p: self.tmp / "t" / p for p in PROJECTS}

    def manifest(self):
        return json.loads((self.source / "migration-manifest.json").read_text(encoding="utf-8"))


class FixtureTest(unittest.TestCase):
    def test_committed_fixture_matches_builder(self):
        on_disk = FIXTURE.read_bytes().replace(b"\r\n", b"\n")  # tolerate autocrlf checkouts
        self.assertEqual(on_disk, b"\n".join(build_fixture_lines()) + b"\n")

    def test_fixture_covers_every_rule(self):
        self.assertEqual({r[4] for r in FIXTURE_ROWS},
                         {"goal", "repo", "refkey", "text1", "multi", "default"})


class AttributionTest(MigrateTestBase):
    def test_a_manifest_assigns_each_row(self):
        res = run_migrate(self.source, self.targets)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        rows = self.manifest()["rows"]
        self.assertEqual(len(rows), len(FIXTURE_ROWS))
        raw_lines = build_fixture_lines()
        for i, (row, expect) in enumerate(zip(rows, FIXTURE_ROWS), start=1):
            self.assertEqual(row["src_seq"], i)
            self.assertEqual(row["src_sha"], line_sha(raw_lines[i - 1]))
            self.assertEqual(row["project"], expect[3], f"row {i}")
            self.assertEqual(row["rule"], expect[4], f"row {i}")

    def test_b_counts_sum_and_each_seq_once(self):
        run_migrate(self.source, self.targets)
        seen = []
        total = 0
        for project, target in self.targets.items():
            events = read_store(target)[1:-1]
            total += len(events)
            self.assertEqual(events and events[0]["refs"]["split_rule"] is not None, bool(events))
            seen += [e["refs"]["src_seq"] for e in events]
        self.assertEqual(total, len(FIXTURE_ROWS))
        self.assertEqual(sorted(seen), list(range(1, len(FIXTURE_ROWS) + 1)))

    def test_c_copies_are_faithful_with_genesis_and_close(self):
        res = run_migrate(self.source, self.targets)
        raw_lines = build_fixture_lines()
        by_seq = {}
        for project, target in self.targets.items():
            store = read_store(target)
            self.assertEqual(store[0]["kind"], "migrated_genesis")
            self.assertEqual(store[-1]["kind"], "migrated_close")
            self.assertEqual(store[-1]["refs"]["count"], len(store) - 2)
            self.assertEqual(store[0]["refs"]["src_count"], len(FIXTURE_ROWS))
            self.assertEqual(store[0]["refs"]["src_head"], line_sha(raw_lines[-1]))
            prev = ""
            for n, ev in enumerate(store, start=1):
                self.assertEqual(ev["seq"], n)
                self.assertEqual(ev["prev"], prev)
                self.assertEqual(ev["v"], 2)
                prev = line_sha(
                    (Path(self.targets[project]) / "events" / "0000-genesis.jsonl")
                    .read_bytes().split(b"\n")[n - 1])
            for ev in store[1:-1]:
                by_seq[ev["refs"]["src_seq"]] = (project, ev)
        for seq, (project, ev) in by_seq.items():
            src = json.loads(raw_lines[seq - 1])
            for key in ("ts", "kind", "actor", "session", "text"):
                self.assertEqual(ev[key], src[key])
            self.assertEqual(ev["refs"]["src_sha"], line_sha(raw_lines[seq - 1]))
            kept = {k: v for k, v in ev["refs"].items()
                    if k not in ("src_seq", "src_sha", "split_rule", "mentions")}
            self.assertEqual(kept, src["refs"])
        self.assertEqual(res.stdout.strip().splitlines()[-1],
                         "MIGRATION OK source=8 aesop=3 keyrun=2 brain=1 psinasty=2")

    def test_c_multi_goes_whole_to_aesop_with_mentions(self):
        run_migrate(self.source, self.targets)
        multi = [e for e in read_store(self.targets["aesop"])
                 if e.get("refs", {}).get("split_rule") == "multi"]
        self.assertEqual(len(multi), 1)
        self.assertEqual(multi[0]["kind"], "checkpoint")
        self.assertEqual(multi[0]["refs"]["mentions"], ["aesop", "keyrun"])
        for project in ("keyrun", "brain", "psinasty"):
            self.assertFalse([e for e in read_store(self.targets[project])
                              if e["refs"].get("src_seq") == 5])


class RefusalTest(MigrateTestBase):
    def assert_nothing_written(self):
        self.assertFalse((self.source / "migration-manifest.json").exists())
        for target in self.targets.values():
            self.assertFalse(list(target.rglob("*.jsonl")) if target.exists() else [])

    def test_d_unfrozen_source_refuses(self):
        write_source(self.source, build_fixture_lines(FIXTURE_ROWS[:-1]))
        res = run_migrate(self.source, self.targets)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assert_nothing_written()

    def test_d_last_note_without_frozen_ref_refuses(self):
        rows = FIXTURE_ROWS[:-1] + [("note", "FROZEN: split", {}, "aesop", "default")]
        write_source(self.source, build_fixture_lines(rows))
        self.assertEqual(run_migrate(self.source, self.targets).returncode, 1)
        self.assert_nothing_written()

    def test_d_broken_source_chain_refuses(self):
        lines = build_fixture_lines()
        obj = json.loads(lines[2])
        obj["text"] = "tampered"
        lines[2] = dump(obj)
        write_source(self.source, lines)
        self.assertEqual(run_migrate(self.source, self.targets).returncode, 1)
        self.assert_nothing_written()

    def test_e_rerun_into_nonempty_target_refuses(self):
        self.assertEqual(run_migrate(self.source, self.targets).returncode, 0)
        before = {p: (t / "events" / "0000-genesis.jsonl").read_bytes()
                  for p, t in self.targets.items()}
        manifest_before = (self.source / "migration-manifest.json").read_bytes()
        res = run_migrate(self.source, self.targets)
        self.assertEqual(res.returncode, 1, res.stdout)
        for p, t in self.targets.items():
            self.assertEqual((t / "events" / "0000-genesis.jsonl").read_bytes(), before[p])
        self.assertEqual((self.source / "migration-manifest.json").read_bytes(), manifest_before)

    def test_f_dry_run_prints_counts_and_writes_nothing(self):
        res = run_migrate(self.source, self.targets, "--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr)
        for line in ("aesop=3", "keyrun=2", "brain=1", "psinasty=2"):
            self.assertIn(line, res.stdout)
        for rule in ("goal", "repo", "refkey", "text1", "multi", "default"):
            self.assertIn(rule, res.stdout)
        self.assert_nothing_written()


if __name__ == "__main__":
    unittest.main()
