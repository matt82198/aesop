"""Pins the S3 GATE and S4 SHIP stage rows of LANE-CONTRACT.md for aesop.

Facts pinned (each traced to a fleet finding row):
  (a) f-fa44a885: LANE-CONTRACT.md must not reference tools/fastcheck.ps1
      (fastcheck is keyrun-only; aesop has no fastcheck).
  (b) f-29cf8cf0: the S3 GATE row names the aesop gate command list verbatim
      and states that no whole-suite discover run is part of S3.
  (c) f-8f98d3d5: the S4 SHIP row orders "commit the staged files with the
      message file" before "merge origin/main (three-way)" and says stash is
      never used.

The stage rows live under the "## Stage lanes (G5)" heading; the S3 row is the
line beginning "   - S3 GATE:" and the S4 row the line beginning "   - S4 SHIP:".
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "LANE-CONTRACT.md")
STAGE_HEADING = "## Stage lanes (G5)"


def _read_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        return handle.read()


def _stage_section(text):
    """Return the text from the Stage lanes heading to end of file."""
    start = text.find(STAGE_HEADING)
    if start < 0:
        raise AssertionError(
            "heading %r not found in LANE-CONTRACT.md" % STAGE_HEADING
        )
    return text[start:]


def _stage_row(section, stage_label):
    """Return the single bullet line for one stage (e.g. 'S3 GATE')."""
    pattern = re.compile(r"^\s*-\s*" + re.escape(stage_label) + r":.*$", re.MULTILINE)
    match = pattern.search(section)
    if match is None:
        raise AssertionError(
            "row %r not found under %r" % (stage_label, STAGE_HEADING)
        )
    return match.group(0)


class TestLaneContractStageRows(unittest.TestCase):

    def test_a_contract_does_not_reference_fastcheck(self):
        # f-fa44a885: fastcheck is keyrun-only; aesop has no tools/fastcheck.ps1.
        text = _read_contract()
        self.assertNotIn(
            "tools/fastcheck.ps1",
            text,
            "LANE-CONTRACT.md references tools/fastcheck.ps1, which is keyrun-only "
            "and does not exist in aesop; remove or replace those references",
        )

    def test_b_s3_gate_row_names_aesop_gate_command_list(self):
        # f-29cf8cf0: S3 GATE must name the aesop gate command list verbatim.
        row = _stage_row(_stage_section(_read_contract()), "S3 GATE")
        required = [
            "node --test",
            "python tools/claudemd_sync_gate.py --check --base-ref origin/main",
            "python tools/claudemd_lint.py --root .",
            "python tools/gen_tool_index.py --check",
            "python -m pyflakes",
            "node --check bin/cli.js",
            "python ~/scripts/secret_scan.py",
        ]
        for command in required:
            self.assertIn(
                command,
                row,
                "S3 GATE row is missing gate command %r; row was: %s" % (command, row),
            )
        self.assertRegex(
            row,
            r"(?i)no\s+whole[- ]suite\s+(unittest\s+)?discover",
            "S3 GATE row must state that no whole-suite discover run is part of S3; "
            "row was: %s" % row,
        )

    def test_c_s4_ship_row_orders_commit_before_merge_and_forbids_stash(self):
        # f-8f98d3d5: S4 SHIP orders commit before three-way merge; stash never used.
        row = _stage_row(_stage_section(_read_contract()), "S4 SHIP")
        commit_phrase = "commit the staged files with the message file"
        merge_phrase = "merge origin/main (three-way)"
        self.assertIn(
            commit_phrase,
            row,
            "S4 SHIP row must say %r; row was: %s" % (commit_phrase, row),
        )
        self.assertIn(
            merge_phrase,
            row,
            "S4 SHIP row must say %r; row was: %s" % (merge_phrase, row),
        )
        self.assertLess(
            row.index(commit_phrase),
            row.index(merge_phrase),
            "S4 SHIP row must order %r BEFORE %r; row was: %s"
            % (commit_phrase, merge_phrase, row),
        )
        self.assertRegex(
            row,
            r"(?i)(never|no)\s+(use\s+)?(git\s+)?stash|stash\s+(is\s+)?never",
            "S4 SHIP row must state that stash is never used; row was: %s" % row,
        )


if __name__ == "__main__":
    unittest.main()
