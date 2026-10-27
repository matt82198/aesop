"""Regression guard for tracker item 89ddff415b30: links in named docs must resolve.

Behavioural check: every markdown link target and every backticked repo-relative
file path in the docs the item names is resolved against the working tree on disk.
A host-root target ("/x/y.md") is always broken on GitHub, because it resolves to
the domain root rather than the repo.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TARGET_DOCS = (
    "docs/archive/WHY-CRASH-ONLY.md",
    "docs/case-study-portfolio.md",
    "docs/REMOTE-ACCESS.md",
)

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_PATH = re.compile(
    r"`([A-Za-z0-9_.\-]+/[A-Za-z0-9_./\-]+\.(?:md|py|tsx?|mjs|js|sh|json))`"
)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def check_doc(doc: Path) -> tuple[int, list[str]]:
    """Return (number of targets checked, list of broken targets) for one doc."""
    text = doc.read_text(encoding="utf-8")
    checked = 0
    broken: list[str] = []
    for target in MD_LINK.findall(text):
        if target.startswith(EXTERNAL_PREFIXES):
            continue
        checked += 1
        path_part = target.split("#", 1)[0]
        if path_part.startswith("/"):
            broken.append(f"[link] {target} (host-root path, not a repo path)")
        elif not (doc.parent / path_part).exists():
            broken.append(f"[link] {target}")
    for span in CODE_PATH.findall(text):
        checked += 1
        if not (REPO_ROOT / span).exists():
            broken.append(f"[path] `{span}`")
    return checked, broken


class TestTrackerDocLinks(unittest.TestCase):
    """Every link and repo path in the named docs must resolve."""

    def test_named_docs_have_no_broken_links(self):
        """Each named doc resolves all targets; a zero-target doc means the scanner is blind."""
        failures: dict[str, list[str]] = {}
        for rel in TARGET_DOCS:
            checked, broken = check_doc(REPO_ROOT / rel)
            self.assertGreater(checked, 0, f"{rel}: scanner found no targets (vacuous check)")
            if broken:
                failures[rel] = broken
        self.assertEqual(failures, {}, "broken doc references:\n" + "\n".join(
            f"{rel}:\n  " + "\n  ".join(items) for rel, items in failures.items()
        ))


if __name__ == "__main__":
    unittest.main()
