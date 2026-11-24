"""Look up the reported v4 extraction failure using the CI catalog's signature column."""
from pathlib import Path
import unittest


class GateBaseCatalogTest(unittest.TestCase):
    def test_find_recipe_from_gate_base_error(self):
        catalog = Path(__file__).resolve().parents[1] / "docs" / "CI-CATALOG.md"
        diagnostic = "the base gate copies cannot run (trunk or HEAD tools/ unreadable)"
        rows = [line.strip("|").split("|") for line in catalog.read_text(encoding="utf-8").splitlines()
                if line.startswith("| CI-")]
        matches = [row for row in rows if diagnostic in row[2]]
        self.assertEqual(len(matches), 1, "The gate-base error should lead to one catalog recipe")
        _, _, signature, cause, recipe, fixed_by, guard = matches[0]
        self.assertIn("Permission denied", signature)
        self.assertIn("v4", cause)
        self.assertIn("hook", recipe.lower())
        self.assertIn("#1207", fixed_by)
        for path in guard.strip().strip("`").split():
            self.assertTrue((catalog.parents[1] / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
