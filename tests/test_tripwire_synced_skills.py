#!/usr/bin/env python3
"""Tests for ~/.claude/skills/synced narrow allowlist in tools/test_isolation_tripwire.py.

Claude Code periodically syncs first-party cloud skill bundles into
~/.claude/skills/synced/<org_uuid>_<user_uuid>/... and .bucket-<org_uuid>_<user_uuid>
(gitignored in ~/.claude/.gitignore). Those runtime cache updates must be
classified and logged via INFO/VERDICT without failing the tripwire, while ANY
mutation to user-authored ~/.claude/skills/<name>/SKILL.md or non-bucket paths
under ~/.claude/skills/synced/ MUST still fail closed.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_test_isolation_tripwire import _fail_bullets, run_tripwire  # noqa: E402

BUCKET_ID = "0c66f2f6-16bd-4292-a788-662e237941e9_970f9bac-9d8d-4b81-8a3c-4820c96a1c45"


def _make_profile(tmp: Path) -> Path:
    root = tmp / "fake-home"
    (root / ".claude" / "skills" / "power").mkdir(parents=True)
    (root / ".claude" / "skills" / "power" / "SKILL.md").write_text("# power\n", encoding="utf-8")
    bucket_dir = root / ".claude" / "skills" / "synced" / BUCKET_ID
    bucket_dir.mkdir(parents=True)
    (bucket_dir / ".last-complete-round").write_text("1\n", encoding="utf-8")
    (bucket_dir / "manifest.json").write_text('{"round": 1}\n', encoding="utf-8")
    (root / ".claude" / "skills" / "synced" / f".bucket-{BUCKET_ID}").write_text(
        "ok\n", encoding="utf-8"
    )
    (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    return root


class TestTripwireSyncedSkillsAllowlist(unittest.TestCase):
    def test_synced_bucket_updates_are_logged_and_allowed(self):
        """Cloud-synced bucket marker/manifest/skill updates under skills/synced/<uuid>_<uuid>/ pass and log INFO+VERDICT."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            round_file = root / ".claude" / "skills" / "synced" / BUCKET_ID / ".last-complete-round"
            new_skill = root / ".claude" / "skills" / "synced" / BUCKET_ID / "docx" / "SKILL.md"
            bucket_marker = root / ".claude" / "skills" / "synced" / f".bucket-{BUCKET_ID}"

            cmd = [
                sys.executable,
                "-c",
                (
                    "import pathlib, sys; "
                    "pathlib.Path(sys.argv[1]).write_text('2\\n', encoding='utf-8'); "
                    "p = pathlib.Path(sys.argv[2]); p.parent.mkdir(parents=True, exist_ok=True); "
                    "p.write_text('# docx\\n', encoding='utf-8'); "
                    "pathlib.Path(sys.argv[3]).write_text('ok2\\n', encoding='utf-8')"
                ),
                str(round_file),
                str(new_skill),
                str(bucket_marker),
            ]
            proc = run_tripwire(root, cmd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_fail_bullets(proc.stderr), [], proc.stderr)
            self.assertIn("[test_isolation_tripwire] INFO: ~/.claude/skills/synced/", proc.stderr)
            self.assertIn("[test_isolation_tripwire] VERDICT:", proc.stderr)

    def test_real_skill_mutation_still_fails_closed_even_when_synced_changes(self):
        """A test overwriting ~/.claude/skills/power/SKILL.md must STILL fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            round_file = root / ".claude" / "skills" / "synced" / BUCKET_ID / ".last-complete-round"
            real_skill = root / ".claude" / "skills" / "power" / "SKILL.md"

            cmd = [
                sys.executable,
                "-c",
                (
                    "import pathlib, sys; "
                    "pathlib.Path(sys.argv[1]).write_text('2\\n', encoding='utf-8'); "
                    "pathlib.Path(sys.argv[2]).write_text('CLOBBERED\\n', encoding='utf-8')"
                ),
                str(round_file),
                str(real_skill),
            ]
            proc = run_tripwire(root, cmd)
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            bullets = "\n".join(_fail_bullets(proc.stderr))
            self.assertIn("skills/power/SKILL.md", bullets, proc.stderr)
            self.assertNotIn(BUCKET_ID, bullets, proc.stderr)

    def test_non_bucket_file_under_skills_synced_still_fails_closed(self):
        """Arbitrary non-UUID paths under ~/.claude/skills/synced/ are NOT allowlisted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            rogue = root / ".claude" / "skills" / "synced" / "rogue-test-output.md"

            cmd = [
                sys.executable,
                "-c",
                "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('bad\\n', encoding='utf-8')",
                str(rogue),
            ]
            proc = run_tripwire(root, cmd)
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("skills/synced/rogue-test-output.md", "\n".join(_fail_bullets(proc.stderr)))


if __name__ == "__main__":
    unittest.main()
