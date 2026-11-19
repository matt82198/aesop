"""Offline tests for the harness-neutral comparison corpus (bench/harness-compare).

Every task must be a real discriminator: its seed working copy FAILS the hidden
check and its known-good reference PASSES it. Nothing here calls a model.
"""

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HC_DIR = REPO_ROOT / "bench" / "harness-compare"
if str(HC_DIR) not in sys.path:
    sys.path.insert(0, str(HC_DIR))

EXPECTED_CATEGORIES = {
    "bug-fix": 3,
    "feature-test-first": 2,
    "refactor": 2,
    "multi-file": 2,
    "read-answer": 1,
    "niche-nuance": 2,
}

# Prompts must not name a harness, a tool, a model, or an aesop-specific term.
FORBIDDEN_PROMPT_WORDS = re.compile(
    r"\b(aesop|claude|anthropic|antigravity|agy|gemini|openai|gpt|haiku|sonnet|opus"
    r"|model|models|tool|tools|pytest|ruff|mypy)\b",
    re.IGNORECASE,
)


def _hc():
    import harness_corpus

    return harness_corpus


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.hc = _hc()
        self.rows = self.hc.load_manifest()

    def test_manifest_has_twelve_rows_with_required_fields(self):
        self.assertEqual(len(self.rows), 12)
        required = {"id", "category", "title", "seed", "prompt", "check", "timeout_s", "tags"}
        for row in self.rows:
            with self.subTest(task=row.get("id")):
                self.assertTrue(required.issubset(row.keys()))
                self.assertIsInstance(row["timeout_s"], int)
                self.assertGreater(row["timeout_s"], 0)
                self.assertIsInstance(row["tags"], list)
                self.assertRegex(row["id"], r"^[a-z0-9-]+$")

    def test_ids_are_unique(self):
        ids = [row["id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_category_spread_matches_spec(self):
        counts = {}
        for row in self.rows:
            counts[row["category"]] = counts.get(row["category"], 0) + 1
        self.assertEqual(counts, EXPECTED_CATEGORIES)

    def test_referenced_paths_exist(self):
        for row in self.rows:
            with self.subTest(task=row["id"]):
                task_dir = self.hc.task_dir(row)
                self.assertEqual(row["seed"], "corpus/tasks/%s/seed" % row["id"])
                self.assertEqual(row["prompt"], "corpus/tasks/%s/prompt.md" % row["id"])
                self.assertTrue((HC_DIR / row["seed"]).is_dir())
                self.assertTrue((HC_DIR / row["prompt"]).is_file())
                self.assertTrue((task_dir / "check" / "check.py").is_file())
                self.assertTrue((task_dir / "reference").is_dir())
                self.assertTrue(any(p.is_file() for p in (task_dir / "reference").rglob("*")))

    def test_seed_holds_no_hidden_checker_files(self):
        for row in self.rows:
            with self.subTest(task=row["id"]):
                task_dir = self.hc.task_dir(row)
                for hidden in (task_dir / "check").rglob("*"):
                    if hidden.is_file():
                        rel = hidden.relative_to(task_dir / "check")
                        self.assertFalse((task_dir / "seed" / rel).exists())

    def test_check_command_runs_python_against_task_check_dir(self):
        for row in self.rows:
            with self.subTest(task=row["id"]):
                self.assertTrue(
                    row["check"].startswith("python {task_dir}/check/"),
                    row["check"],
                )

    def test_prompt_is_harness_neutral_and_states_done_condition(self):
        for row in self.rows:
            with self.subTest(task=row["id"]):
                text = (HC_DIR / row["prompt"]).read_text(encoding="utf-8")
                self.assertIsNone(FORBIDDEN_PROMPT_WORDS.search(text))
                self.assertIn("## Done when", text)

    def test_seed_check_and_reference_are_stdlib_only(self):
        for row in self.rows:
            with self.subTest(task=row["id"]):
                task_dir = self.hc.task_dir(row)
                local = {p.stem for p in task_dir.rglob("*.py")}
                local |= {p.name for p in task_dir.rglob("*") if p.is_dir()}
                allowed = set(sys.stdlib_module_names) | local
                for sub in ("seed", "check", "reference"):
                    for py in (task_dir / sub).rglob("*.py"):
                        tree = ast.parse(py.read_text(encoding="utf-8"))
                        for node in ast.walk(tree):
                            names = []
                            if isinstance(node, ast.Import):
                                names = [alias.name for alias in node.names]
                            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                                names = [node.module]
                            for name in names:
                                self.assertIn(name.split(".")[0], allowed, "%s imports %s" % (py, name))


class SeedFailsReferencePassesTests(unittest.TestCase):
    def test_seed_fails_check_and_reference_passes(self):
        hc = _hc()
        for row in hc.load_manifest():
            with self.subTest(task=row["id"]):
                with tempfile.TemporaryDirectory() as tmp:
                    seed_wd = hc.prepare_task(row, Path(tmp) / "seed")
                    seed_result = hc.run_check(row, seed_wd)
                    self.assertFalse(seed_result.passed, "seed passes: " + seed_result.reason)
                    ref_wd = hc.prepare_task(row, Path(tmp) / "reference")
                    hc.overlay_reference(row, ref_wd)
                    ref_result = hc.run_check(row, ref_wd)
                    self.assertTrue(ref_result.passed, "reference fails: " + ref_result.reason)
                    self.assertEqual(ref_result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
