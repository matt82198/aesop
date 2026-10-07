#!/usr/bin/env python3
"""Test suite for pr_footprint_gate.py."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


GATE_PATH = Path(__file__).parent.parent / 'tools' / 'pr_footprint_gate.py'


def run_gate(base_sha, head_sha, labels=None, repo_path=None):
    """Run pr_footprint_gate and return (exit_code, stdout, stderr)."""
    cmd = [
        sys.executable, str(GATE_PATH),
        '--base', base_sha,
        '--head', head_sha,
    ]
    if labels:
        cmd.extend(['--labels', json.dumps(labels)])

    result = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    return result.returncode, result.stdout, result.stderr


def setup_test_repo():
    """Create a temporary git repo with test commits."""
    tmpdir = tempfile.mkdtemp(prefix='footprint_test_')
    repo_path = Path(tmpdir)

    subprocess.run(['git', 'init'], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=repo_path, capture_output=True, check=True)

    (repo_path / 'README.md').write_text('# Test Repo\n')
    subprocess.run(['git', 'add', 'README.md'], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=repo_path, capture_output=True, check=True)
    base_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

    return repo_path, base_sha


class TestPRFootprintGate(unittest.TestCase):
    """Test cases for pr_footprint_gate."""

    def test_small_diff_passes(self):
        """Small diff (few files, few deletions) should pass."""
        repo_path, base_sha = setup_test_repo()

        (repo_path / 'file1.txt').write_text('content1\n')
        (repo_path / 'file2.txt').write_text('content2\n')
        subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add two files'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        exit_code, stdout, stderr = run_gate(base_sha, head_sha, repo_path=repo_path)
        self.assertEqual(exit_code, 0, f"Small diff should pass. stdout: {stdout}, stderr: {stderr}")

    def test_too_many_files_fails(self):
        """PR with >100 files should fail."""
        repo_path, base_sha = setup_test_repo()

        for i in range(101):
            (repo_path / f'file{i}.txt').write_text(f'content{i}\n')
        subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add 101 files'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        exit_code, stdout, stderr = run_gate(base_sha, head_sha, repo_path=repo_path)
        self.assertEqual(exit_code, 1, f"101 files should fail. stdout: {stdout}, stderr: {stderr}")
        self.assertIn('files', stdout.lower(), f"Output should mention files: {stdout}")

    def test_too_many_deletions_fails(self):
        """PR with >5000 deletions should fail."""
        repo_path, base_sha = setup_test_repo()

        (repo_path / 'bigfile.txt').write_text('\n'.join(['line'] * 5001) + '\n')
        subprocess.run(['git', 'add', 'bigfile.txt'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add big file'], cwd=repo_path, capture_output=True, check=True)
        base_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        (repo_path / 'bigfile.txt').unlink()
        subprocess.run(['git', 'add', 'bigfile.txt'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Delete big file'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        exit_code, stdout, stderr = run_gate(base_sha, head_sha, repo_path=repo_path)
        self.assertEqual(exit_code, 1, f"5001 deletions should fail. stdout: {stdout}, stderr: {stderr}")
        self.assertIn('deletion', stdout.lower(), f"Output should mention deletions: {stdout}")

    def test_big_change_label_bypasses(self):
        """PR with big-change label should bypass file/deletion limits."""
        repo_path, base_sha = setup_test_repo()

        for i in range(101):
            (repo_path / f'file{i}.txt').write_text(f'content{i}\n')
        subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Add 101 files'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        labels = [{'name': 'big-change'}]
        exit_code, stdout, stderr = run_gate(base_sha, head_sha, labels=labels, repo_path=repo_path)
        self.assertEqual(exit_code, 0, f"big-change label should bypass. stdout: {stdout}, stderr: {stderr}")

    def test_fixture_commit_fails(self):
        """Commit with fixture pattern in subject should fail, even with small diff."""
        repo_path, base_sha = setup_test_repo()

        (repo_path / 'file.txt').write_text('content\n')
        subprocess.run(['git', 'add', 'file.txt'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        exit_code, stdout, stderr = run_gate(base_sha, head_sha, repo_path=repo_path)
        self.assertEqual(exit_code, 1, f"Fixture commit should fail. stdout: {stdout}, stderr: {stderr}")
        self.assertIn('fixture', stdout.lower(), f"Output should mention fixture: {stdout}")

    def test_chore_stats_only_passes(self):
        """PR with only chore(stats) commits should pass, even with size."""
        repo_path, base_sha = setup_test_repo()

        for i in range(101):
            (repo_path / f'file{i}.txt').write_text(f'content{i}\n')
        subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'chore(stats): update metrics'], cwd=repo_path, capture_output=True, check=True)
        head_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path, text=True).strip()

        exit_code, stdout, stderr = run_gate(base_sha, head_sha, repo_path=repo_path)
        self.assertEqual(exit_code, 0, f"chore(stats) should pass. stdout: {stdout}, stderr: {stderr}")


if __name__ == '__main__':
    unittest.main()
