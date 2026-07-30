#!/usr/bin/env python3
"""
Tests for tools/workflow_model_linter.py — Guardrail G7 workflow model pin enforcement.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestWorkflowModelLinter(unittest.TestCase):
    """Test workflow_model_linter.py CLI and core functions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_linter(self, args: List[str]) -> Tuple[int, str, str]:
        """Run the linter CLI and return (exit_code, stdout, stderr)."""
        cmd = [sys.executable, 'tools/workflow_model_linter.py'] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        return result.returncode, result.stdout, result.stderr

    def test_help_exits_0(self):
        """--help flag should exit 0."""
        exit_code, stdout, stderr = self.run_linter(['--help'])
        assert exit_code == 0
        assert 'Guardrail G7' in stdout or 'workflow_model_linter' in stdout or 'model pin' in stdout

    def test_unknown_flag_exits_2(self):
        """Unknown flags should exit 2."""
        exit_code, stdout, stderr = self.run_linter(['--unknown-flag'])
        assert exit_code == 2
        assert 'unknown flag' in stderr

    def test_no_files_exits_0(self):
        """Scan with no matching files should exit 0."""
        # Create a temp dir with no .js/.mjs files
        subdir = self.temp_path / 'empty_dir'
        subdir.mkdir()
        (subdir / 'file.txt').write_text('not javascript')

        exit_code, stdout, stderr = self.run_linter([str(subdir)])
        assert exit_code == 0
        assert '[]' in stdout or not stdout.strip() or 'VIOLATION' not in stdout

    def test_agent_call_with_model_haiku_passes(self):
        """agent() call with model:'haiku' should pass."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent(
  "some prompt",
  { label: 'test', phase: 'Build', model: 'haiku', schema: { type: 'object' } }
)
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 0
        assert 'VIOLATION' not in stdout

    def test_agent_call_with_model_sonnet_passes(self):
        """agent() call with model:'sonnet' should pass (any model counts)."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent(
  "some prompt",
  { model: 'sonnet', label: 'test' }
)
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 0
        assert 'VIOLATION' not in stdout

    def test_agent_call_without_model_fails(self):
        """agent() call without model parameter should fail."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent(
  "some prompt",
  { label: 'test', phase: 'Build', schema: { type: 'object' } }
)
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 1
        assert 'VIOLATION' in stdout
        assert str(js_file.name) in stdout or 'test.mjs' in stdout

    def test_suppression_marker_model_ok_comment(self):
        """agent() with // model-ok comment should be suppressed."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent("prompt", { label: 'test' }) // model-ok
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 0
        assert 'VIOLATION' not in stdout

    def test_json_output_format(self):
        """--json flag should output findings as JSON array."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent("prompt", { label: 'test' })
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path), '--json'])
        assert exit_code == 1

        # Parse JSON output
        try:
            findings = json.loads(stdout)
            assert isinstance(findings, list)
            assert len(findings) == 1
            finding = findings[0]
            assert 'path' in finding
            assert 'line' in finding
            assert 'call' in finding
            assert 'message' in finding
            assert finding['message'] == 'agent() call without model pin'
        except json.JSONDecodeError as e:
            raise AssertionError(f'Invalid JSON output: {e}')

    def test_multiple_violations_in_one_file(self):
        """File with multiple violations should report all of them."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const r1 = await agent("prompt1", { label: 'test1' })
const r2 = await agent("prompt2", { label: 'test2' })
const r3 = await agent("prompt3", { model: 'haiku', label: 'test3' })
const r4 = await agent("prompt4", { label: 'test4' })
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 1

        # Should report 3 violations (lines 2, 3, 5 have no model)
        violation_count = stdout.count('VIOLATION')
        assert violation_count >= 3

    def test_agent_call_with_model_on_next_line(self):
        """agent() with model parameter on next line should pass."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent(
  "some prompt",
  {
    label: 'test',
    model: 'haiku',
    schema: { type: 'object' }
  }
)
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 0
        assert 'VIOLATION' not in stdout

    def test_model_parameter_with_spaces(self):
        """model parameter with spaces around : should be detected."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text('''
const result = await agent("prompt", { model : 'haiku', label: 'test' })
''')

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        assert exit_code == 0
        assert 'VIOLATION' not in stdout

    def test_scan_multiple_directories(self):
        """Should scan multiple provided directories."""
        dir1 = self.temp_path / 'dir1'
        dir2 = self.temp_path / 'dir2'
        dir1.mkdir()
        dir2.mkdir()

        # File in dir1 with violation
        (dir1 / 'test1.mjs').write_text('agent("p", {label: "test"})')

        # File in dir2 with model pin (clean)
        (dir2 / 'test2.mjs').write_text('agent("p", {model: "haiku"})')

        exit_code, stdout, stderr = self.run_linter([str(dir1), str(dir2)])
        assert exit_code == 1  # Should fail due to violation in dir1
        assert 'test1.mjs' in stdout or 'VIOLATION' in stdout
        assert 'test2.mjs' not in stdout or 'test2.mjs' not in stdout.split('VIOLATION')[0]

    def test_scan_specific_file(self):
        """Should scan a specific file when given directly."""
        js_file = self.temp_path / 'specific.mjs'
        js_file.write_text('agent("prompt", {label: "test"})')

        exit_code, stdout, stderr = self.run_linter([str(js_file)])
        assert exit_code == 1
        assert 'VIOLATION' in stdout

    def test_nonexistent_path_exits_2(self):
        """Nonexistent path should exit 2."""
        nonexistent = self.temp_path / 'nonexistent'
        exit_code, stdout, stderr = self.run_linter([str(nonexistent)])
        assert exit_code == 2
        assert 'does not exist' in stderr

    def test_agent_call_multiple_on_same_line(self):
        """Multiple agent() calls on same line should be detected."""
        js_file = self.temp_path / 'test.mjs'
        js_file.write_text(
            'const a = agent("p1", {label: "l1"}); const b = agent("p2", {model: "haiku"})'
        )

        exit_code, stdout, stderr = self.run_linter([str(self.temp_path)])
        # Should detect the first call as violation
        assert exit_code == 1
        assert 'VIOLATION' in stdout


if __name__ == '__main__':
    unittest.main()
