#!/usr/bin/env python3
"""
Unit tests for tools/hook_tool_manifest.py

The gap under test: hooks/pre-push-policy.sh dispatches to N tools under
$aesop_root/tools/. Seven of its eight tool call sites fail OPEN -- a missing
tool file logs "<gate>_skipped_tool_missing" and returns 0, so deleting or
renaming a gate script silently disarms that gate with no signal. This manifest
gate asserts every tool the hook references exists on disk and is non-empty.

Tests cover:
- Clean manifest (all referenced tools present and non-empty)
- Missing tool file -> exit 1
- Empty (zero-byte) tool file -> exit 1
- Whitespace-only tool file -> exit 1
- Hook with no parseable references -> exit 2 (fail-closed, not vacuous green)
- Missing hook script -> exit 2
- Fixture-write lines (cat > "$AESOP_ROOT/tools/x.py") are not invocations
- Comment-only mentions are not references
- --json output shape
- --list enumerates references without asserting existence
- REAL RUN against the live repository hook
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / 'tools' / 'hook_tool_manifest.py'


class TestHookToolManifest(unittest.TestCase):
    """Test suite for the hook tool-existence manifest gate."""

    def run_tool(self, root, hook_path=None, extra_args=None):
        """Run the manifest gate against a fixture root. Returns (rc, stdout, stderr)."""
        cmd = [sys.executable, str(TOOL), '--root', str(root)]
        if hook_path is not None:
            cmd.extend(['--hook', str(hook_path)])
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', cwd=str(root)
        )
        return result.returncode, result.stdout, result.stderr

    def make_fixture(self, tmpdir, hook_body, tools):
        """
        Build a fixture repo: hooks/pre-push-policy.sh with hook_body, plus a
        tools/ dir populated from `tools` (name -> content, or None to omit).
        Returns (root, hook_path).
        """
        root = Path(tmpdir)
        (root / 'hooks').mkdir(parents=True, exist_ok=True)
        (root / 'tools').mkdir(parents=True, exist_ok=True)
        hook = root / 'hooks' / 'pre-push-policy.sh'
        hook.write_text(hook_body, encoding='utf-8')
        for name, content in tools.items():
            if content is None:
                continue
            (root / 'tools' / name).write_text(content, encoding='utf-8')
        return root, hook

    # ---- clean ----------------------------------------------------------

    def test_clean_all_tools_present(self):
        body = (
            'check_a() {\n'
            '  local s="$aesop_root/tools/alpha.py"\n'
            '  [ -f "$s" ] || return 0\n'
            '}\n'
            'check_b() {\n'
            '  local t="$aesop_root/tools/beta.py"\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(
                tmp, body, {'alpha.py': '# alpha\n', 'beta.py': '# beta\n'}
            )
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 0, f'expected clean exit 0, got {rc}: {out}{err}')
            self.assertIn('alpha.py', out)
            self.assertIn('beta.py', out)

    # ---- missing --------------------------------------------------------

    def test_missing_tool_fails_closed(self):
        body = (
            'check_a() {\n'
            '  local s="$aesop_root/tools/alpha.py"\n'
            '}\n'
            'check_b() {\n'
            '  local t="$aesop_root/tools/ghost.py"\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(
                tmp, body, {'alpha.py': '# alpha\n', 'ghost.py': None}
            )
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 1, f'missing tool must exit 1, got {rc}: {out}{err}')
            self.assertIn('ghost.py', out + err)
            self.assertIn('MISSING', (out + err).upper())

    def test_missing_tool_reported_via_json(self):
        body = 'x() { local s="$aesop_root/tools/ghost.py"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {})
            rc, out, err = self.run_tool(root, hook, ['--json'])
            self.assertEqual(rc, 1)
            data = json.loads(out)
            self.assertEqual(data['status'], 'findings')
            self.assertEqual(data['exit_code'], 1)
            names = [f['tool'] for f in data['findings']]
            self.assertIn('tools/ghost.py', names)
            self.assertEqual(data['findings'][0]['kind'], 'MISSING')

    # ---- empty ----------------------------------------------------------

    def test_zero_byte_tool_fails_closed(self):
        body = 'x() { local s="$aesop_root/tools/hollow.py"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'hollow.py': ''})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 1, f'zero-byte tool must exit 1, got {rc}')
            self.assertIn('EMPTY', (out + err).upper())

    def test_whitespace_only_tool_fails_closed(self):
        body = 'x() { local s="$aesop_root/tools/hollow.py"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'hollow.py': '   \n\n\t\n'})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 1, 'whitespace-only tool is effectively empty')
            self.assertIn('EMPTY', (out + err).upper())

    # ---- fail-closed on parse breakage ----------------------------------

    def test_no_references_is_error_not_vacuous_green(self):
        """
        A hook the parser finds zero tool references in means either the hook
        was gutted or the idiom changed underneath the parser. Reporting exit 0
        there would recreate the exact vacuous-green class this gate exists to
        kill, so it must be exit 2.
        """
        body = 'main() { echo "no tools here"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 2, f'zero references must exit 2, got {rc}')

    def test_missing_hook_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tools').mkdir()
            rc, out, err = self.run_tool(root, root / 'hooks' / 'nope.sh')
            self.assertEqual(rc, 2, f'missing hook must exit 2, got {rc}')

    # ---- reference extraction precision ---------------------------------

    def test_fixture_write_lines_are_not_references(self):
        """
        run_test_mode() writes mock scanners via `cat > "$AESOP_ROOT/tools/x.py"`.
        Those are fixture WRITES inside a temp AESOP_ROOT, not invocations of a
        repo tool, and must not be asserted to exist in the real tools/ dir.
        """
        body = (
            'x() { local s="$aesop_root/tools/alpha.py"; }\n'
            'run_test_mode() {\n'
            '  cat > "$AESOP_ROOT/tools/mock_only.py" <<\'EOF\'\n'
            'print(1)\n'
            'EOF\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'alpha.py': '# a\n'})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 0, f'fixture write must not be a reference: {out}{err}')
            self.assertNotIn('mock_only.py', out)

    def test_comment_mentions_are_not_references(self):
        body = (
            '# tools/commented_only.py is mentioned in prose here\n'
            'x() {\n'
            '  # runs tools/also_prose.py --check\n'
            '  local s="$aesop_root/tools/alpha.py"\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'alpha.py': '# a\n'})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 0, f'{out}{err}')
            self.assertNotIn('commented_only.py', out)
            self.assertNotIn('also_prose.py', out)

    def test_alternate_root_var_spellings_are_captured(self):
        """Both $aesop_root and ${AESOP_ROOT:-$HOME/aesop} idioms appear in the hook."""
        body = (
            'x() { local s="$aesop_root/tools/alpha.py"; }\n'
            'y() { local r="${AESOP_ROOT:-$HOME/aesop}"; local t="$r/tools/beta.py"; }\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'alpha.py': '# a\n', 'beta.py': None})
            rc, out, err = self.run_tool(root, hook)
            self.assertEqual(rc, 1, 'beta.py is referenced via $r and is missing')
            self.assertIn('beta.py', out + err)

    def test_duplicate_references_deduped(self):
        body = (
            'x() { local s="$aesop_root/tools/alpha.py"; }\n'
            'y() { local s="$aesop_root/tools/alpha.py"; }\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {'alpha.py': '# a\n'})
            rc, out, err = self.run_tool(root, hook, ['--json'])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(data['referenced'].count('tools/alpha.py'), 1)

    def test_list_mode_enumerates_without_asserting(self):
        body = 'x() { local s="$aesop_root/tools/ghost.py"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            root, hook = self.make_fixture(tmp, body, {})
            rc, out, err = self.run_tool(root, hook, ['--list'])
            self.assertEqual(rc, 0, '--list is informational, never a gate verdict')
            self.assertIn('tools/ghost.py', out)

    # ---- real run -------------------------------------------------------

    def test_real_repository_hook_manifest_is_intact(self):
        """REAL RUN: every tool the live pre-push hook dispatches to must exist."""
        cmd = [sys.executable, str(TOOL), '--root', str(REPO_ROOT), '--json']
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', cwd=str(REPO_ROOT)
        )
        self.assertEqual(
            result.returncode, 0,
            f'live hook manifest has drift:\n{result.stdout}\n{result.stderr}'
        )
        data = json.loads(result.stdout)
        # The eight known gate dispatch sites in hooks/pre-push-policy.sh.
        for expected in (
            'tools/secret_scan.py',
            'tools/tracker_guard.py',
            'tools/import_resolution_check.py',
            'tools/claudemd_sync_gate.py',
            'tools/metrics_gate.py',
            'tools/verify_test_suite_count.py',
            'tools/encoding_lint.py',
            'tools/verify_test_coverage.py',
        ):
            self.assertIn(expected, data['referenced'],
                          f'{expected} no longer detected as a hook reference')


if __name__ == '__main__':
    unittest.main()
