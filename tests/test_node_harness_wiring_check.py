"""Tests for tools/node_harness_wiring_check.py (G8 cross-site wiring drift guard).

Hermetic: all fixtures are tempdir workflow/package.json files; no network.

Root cause this guards (GAP, 2026-10-06): PR #864 wired
`--import ./tests/helpers/isolated-env.mjs` into TWO of the Node-suite's
invocation sites (ci.yml's ubuntu step via `npm run test:node`, and ci.yml's
windows-shard raw `node --test ...` line) but missed a THIRD site --
.github/workflows/main-full.yml's own raw `node --test ...` invocation, which
predates #864. That line ran the Node suite unisolated on every main-full run,
staying accidentally green on windows-latest (USERPROFILE is an ambient OS env
var there) and going genuinely RED on ubuntu-latest (no such ambient var) --
tests/isolated-home-tripwire.test.mjs caught the SYMPTOM; this gate catches the
SHAPE of the gap (wiring landing at some sites, not all) so a fourth site added
later fails closed instead of drifting silently.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import node_harness_wiring_check as guard  # noqa: E402


WIRED_RAW_LINE = (
    "python tools/test_isolation_tripwire.py -- node --import "
    "./tests/helpers/isolated-env.mjs --test --test-force-exit "
    "--test-timeout=180000 tests/*.test.mjs"
)

# The EXACT line main-full.yml shipped before the fix in this PR -- the real
# regression this tool exists to catch.
BROKEN_RAW_LINE = "node --test --test-force-exit --test-timeout=180000 tests/*.test.mjs"

WIRED_PACKAGE_JSON = json.dumps(
    {
        "scripts": {
            "test": WIRED_RAW_LINE,
            "test:node": WIRED_RAW_LINE,
        }
    }
)

UNWIRED_PACKAGE_JSON = json.dumps(
    {
        "scripts": {
            "test": BROKEN_RAW_LINE,
            "test:node": BROKEN_RAW_LINE,
        }
    }
)


def _write_fixture(tmp_path, workflow_yaml, package_json=WIRED_PACKAGE_JSON, workflow_name="ci.yml"):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / workflow_name).write_text(workflow_yaml, encoding="utf-8")
    (tmp_path / "package.json").write_text(package_json, encoding="utf-8")


class TestNodeHarnessWiringCheck(unittest.TestCase):
    def test_clean_tree_no_findings(self):
        """A workflow whose raw invocation is fully wired reports zero findings."""
        yaml_text = f"""
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Run Node.js tests
        run: {WIRED_RAW_LINE}
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text)
            findings, sites = guard.check(str(tmp_path))
            self.assertEqual(findings, [])
            self.assertGreaterEqual(sites, 1)

    def test_detects_the_real_regression_unwired_raw_invocation(self):
        """The EXACT broken main-full.yml line (no --import, no tripwire) must
        be flagged -- this is the literal pre-fix shape, not a synthetic one."""
        yaml_text = f"""
jobs:
  main-full-verify:
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - name: Run Node.js tests (shard 0 only, both platforms)
        if: matrix.python-shard == 0
        run: {BROKEN_RAW_LINE}
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text, workflow_name="main-full.yml")
            findings, sites = guard.check(str(tmp_path))
            self.assertEqual(len(findings), 2, findings)
            joined = "\n".join(findings)
            self.assertIn("missing `--import ./tests/helpers/isolated-env.mjs`", joined)
            self.assertIn("missing `tools/test_isolation_tripwire.py --` wrapper", joined)
            self.assertIn("main-full.yml:", joined)

    def test_wired_import_but_missing_tripwire_wrapper(self):
        """Partial wiring (--import present, tripwire wrapper absent) is still a
        finding -- G8 requires BOTH pieces, not just the fixture import."""
        partial = "node --import ./tests/helpers/isolated-env.mjs --test tests/*.test.mjs"
        yaml_text = f"""
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Run Node.js tests
        run: {partial}
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text)
            findings, _ = guard.check(str(tmp_path))
            self.assertEqual(len(findings), 1, findings)
            self.assertIn("tripwire", findings[0])

    def test_npm_script_invocation_resolves_through_package_json_clean(self):
        """`run: npm run test:node` is not itself flagged when package.json's
        own test:node script is wired."""
        yaml_text = """
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Run Node.js tests
        run: npm run test:node
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text, package_json=WIRED_PACKAGE_JSON)
            findings, sites = guard.check(str(tmp_path))
            self.assertEqual(findings, [])
            self.assertGreaterEqual(sites, 2)  # the workflow site + package.json's own site

    def test_npm_script_invocation_flags_when_package_json_itself_unwired(self):
        """`npm run test:node` reads clean at the workflow line, but if
        package.json's OWN script is unwired, that must still surface as a
        finding (checked directly against scripts.test:node)."""
        yaml_text = """
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Run Node.js tests
        run: npm run test:node
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text, package_json=UNWIRED_PACKAGE_JSON)
            findings, _ = guard.check(str(tmp_path))
            joined = "\n".join(findings)
            self.assertIn("package.json scripts.test:node", joined)

    def test_fails_closed_on_zero_invocation_sites(self):
        """An inventory that finds NOTHING must error, not silently pass -- the
        real gap was exactly this: a site existing outside what anyone checked."""
        yaml_text = """
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Unrelated step
        run: echo hello
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fixture(tmp_path, yaml_text, package_json=json.dumps({"scripts": {}}))
            with self.assertRaises(guard.CheckError):
                guard.check(str(tmp_path))

    def test_real_repo_tree_is_clean(self):
        """End-to-end regression proof: the ACTUAL repo (not a fixture) has
        every known Node-suite invocation site wired. Run `git stash` the fix
        in this PR and this test goes RED with the main-full.yml finding --
        that is the anti-vacuity proof for this whole gate."""
        findings, sites = guard.check(str(REPO_ROOT))
        self.assertEqual(findings, [], findings)
        self.assertGreaterEqual(sites, 4)  # ci.yml x2, main-full.yml, reproduce.yml (+ package.json)


if __name__ == "__main__":
    unittest.main()
