"""Regression test for f-45a0bd90: docs/CI-CATALOG.md documents the #1280 verify-receipt failure."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_CATALOG = REPO_ROOT / "docs" / "CI-CATALOG.md"
EXPECTED_COLUMNS = (
    "id",
    "category",
    "signature (verbatim)",
    "root cause",
    "recipe (apply once)",
    "fixed-by",
    "recurrence guard",
)


def parse_catalog_rows(catalog_path: Path) -> list[dict[str, str]]:
    """Parse the main markdown table in docs/CI-CATALOG.md into a list of row dicts."""
    lines = catalog_path.read_text(encoding="utf-8").splitlines()
    header_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("| id | category |"):
            header_idx = idx
            break
    if header_idx is None:
        raise AssertionError("CI-CATALOG.md missing expected table header")

    headers = [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[header_idx].strip().strip("|"))]
    if tuple(headers) != EXPECTED_COLUMNS:
        raise AssertionError(f"Unexpected headers: {headers}")

    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) != len(headers):
            raise AssertionError(f"Malformed row ({len(cells)} cells): {line}")
        rows.append(dict(zip(headers, cells)))
    return rows


class TestCiCatalogReceiptIncident(unittest.TestCase):
    """Verify docs/CI-CATALOG.md records the #1280 advisory verify-receipt failure."""

    def test_pr1280_verify_receipt_incident_documented(self):
        """Catalog must contain an incident row for #1280's MISSING receipt failure."""
        rows = parse_catalog_rows(CI_CATALOG)
        matching = [
            row
            for row in rows
            if "#1280" in row["root cause"] or "#1280" in row["category"]
        ]
        self.assertTrue(
            matching,
            "docs/CI-CATALOG.md is missing a dated incident entry for PR #1280 verify-receipt failure",
        )
        row = matching[0]
        self.assertIn(
            "MISSING receipt: no verify-receipt-local receipt on 8479062b42ce4f2467ece8d6466cc7af2a8e7a67",
            row["signature (verbatim)"],
        )
        self.assertIn("2026-10-22", row["root cause"])
        self.assertIn("AESOP_RECEIPT_EMIT=0", row["root cause"])
        self.assertIn("non-required", row["root cause"])
        self.assertTrue(row["recipe (apply once)"])
        self.assertTrue((REPO_ROOT / "docs" / "archive" / "RECEIPT-GATE.md").exists())
        self.assertTrue((REPO_ROOT / "tools" / "emit_receipt.py").exists())


if __name__ == "__main__":
    unittest.main()
