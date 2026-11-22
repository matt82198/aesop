"""Regression guard for finding f-2d678eeb: CI-LINEAR-BASE main-SHA rule and open-PR baseline.

Verifies that docs/CI-CATALOG.md row CI-LINEAR-BASE documents:
1. The 'test at a named main sha' rule (every verify names the main SHA it ran
   against, and how to obtain it via git rev-parse).
2. The dated open-PR count baseline from gh pr list.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_CATALOG = REPO_ROOT / "docs" / "CI-CATALOG.md"


def _parse_ci_catalog_row(text: str, row_id: str) -> dict[str, str]:
    """Parse a markdown table row by id from docs/CI-CATALOG.md."""
    headers: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not headers and cells and cells[0] == "id":
            headers = cells
            continue
        if headers and cells[0] == row_id:
            return dict(zip(headers, cells))
    return {}


class TestCiCatalogLinearBase(unittest.TestCase):
    """CI-LINEAR-BASE must document the named main-SHA rule and open-PR baseline."""

    def test_ci_linear_base_has_main_sha_rule_and_baseline(self):
        """Row CI-LINEAR-BASE states the named main-SHA verify rule and dated open-PR count."""
        text = CI_CATALOG.read_text(encoding="utf-8")
        row = _parse_ci_catalog_row(text, "CI-LINEAR-BASE")
        self.assertTrue(row, "CI-LINEAR-BASE row missing from docs/CI-CATALOG.md")

        recipe = row.get("recipe (apply once)", "")
        row_text = " ".join(row.values())

        self.assertIn(
            "rev-parse",
            recipe,
            "CI-LINEAR-BASE recipe must state how to get the named main SHA (git rev-parse)",
        )
        self.assertRegex(
            row_text,
            r"\b\d{4}-\d{2}-\d{2}\b.*\b\d+\s+open\s+PR",
            "CI-LINEAR-BASE must record a dated open-PR baseline count",
        )


if __name__ == "__main__":
    unittest.main()
