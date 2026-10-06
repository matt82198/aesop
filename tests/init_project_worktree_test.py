#!/usr/bin/env python3
"""
Test suite: init_project.py hook installation in git worktrees

This verifies that tools/init_project.py correctly handles the worktree case
where .git is a FILE (not a directory) containing gitdir pointer.

Worktree scenario:
- Primary repo: /tmp/aesop-test-repo/.git (real directory)
- Worktree: /tmp/aesop-test-repo-wt/.git (FILE containing "gitdir: ../repo/.git/worktrees/name")
- Hooks MUST be installed in the common git dir (../repo/.git/hooks), not worktree
"""

import os
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path
import unittest

# Add tools to path so we can import init_project
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

# Import after path setup
import init_project


class TestInitProjectWorktree(unittest.TestCase):
    """Test init_project.py with real git worktrees"""

    @classmethod
    def setUpClass(cls):
        """Set up test repositories"""
        cls.temp_dir = tempfile.mkdtemp(prefix='aesop-wt-test-')
        cls.repo_dir = Path(cls.temp_dir) / 'test-repo'
        cls.worktree_dir = Path(cls.temp_dir) / 'test-worktree'

        # Initialize primary git repo with initial commit (required for worktree)
        cls.repo_dir.mkdir()
        subprocess.run(['git', 'init', '-q', str(cls.repo_dir)], check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=cls.repo_dir, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=cls.repo_dir, check=True)
        (cls.repo_dir / 'README.md').write_text('test')
        subprocess.run(['git', 'add', '-A'], cwd=cls.repo_dir, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'Initial commit'], cwd=cls.repo_dir, check=True)

        # Create a worktree
        subprocess.run(['git', '-C', str(cls.repo_dir), 'worktree', 'add', str(cls.worktree_dir)], check=True)

    @classmethod
    def tearDownClass(cls):
        """Clean up test repositories"""
        try:
            # Remove worktree
            subprocess.run(['git', '-C', str(cls.repo_dir), 'worktree', 'prune', '-v'],
                          capture_output=True)
        except Exception:
            pass

        # Remove temp directory
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_worktree_git_is_file(self):
        """Verify worktree .git is a file, not a directory"""
        git_path = self.worktree_dir / '.git'
        self.assertTrue(git_path.exists(), '.git should exist in worktree')
        self.assertTrue(git_path.is_file(), '.git should be a file in worktree')
        self.assertFalse(git_path.is_dir(), '.git should NOT be a directory in worktree')

    def test_worktree_git_contains_gitdir(self):
        """Verify worktree .git file contains gitdir pointer"""
        git_path = self.worktree_dir / '.git'
        content = git_path.read_text(encoding='utf-8')

        self.assertIn('gitdir:', content, '.git file should contain gitdir pointer')
        self.assertIn('/worktrees/', content, 'gitdir pointer should reference worktree path')

    def test_resolve_real_git_dir_worktree(self):
        """Test resolve_real_git_dir function with worktree"""
        resolved_git_dir = init_project.resolve_real_git_dir(str(self.worktree_dir))

        self.assertIsNotNone(resolved_git_dir, 'Should resolve git dir for worktree')
        self.assertTrue(resolved_git_dir.exists(), f'Resolved git dir should exist: {resolved_git_dir}')
        self.assertTrue((resolved_git_dir / 'hooks').exists(), 'Hooks dir should exist in resolved git dir')

    def test_resolve_real_git_dir_regular_repo(self):
        """Test resolve_real_git_dir function with regular repo"""
        resolved_git_dir = init_project.resolve_real_git_dir(str(self.repo_dir))

        self.assertIsNotNone(resolved_git_dir, 'Should resolve git dir for regular repo')
        self.assertTrue(resolved_git_dir.exists(), f'Resolved git dir should exist: {resolved_git_dir}')
        self.assertEqual(resolved_git_dir, self.repo_dir / '.git', 'Should resolve to .git for regular repo')

    def test_resolve_real_git_dir_nonexistent(self):
        """Test resolve_real_git_dir with non-git directory"""
        no_git_dir = Path(self.temp_dir) / 'no-git'
        no_git_dir.mkdir()

        resolved_git_dir = init_project.resolve_real_git_dir(str(no_git_dir))
        self.assertIsNone(resolved_git_dir, 'Should return None for non-git directory')

    def test_install_hook_in_worktree(self):
        """Test that hooks are installed in common git dir for worktree"""
        # Resolve common git dir
        common_git_dir = init_project.resolve_real_git_dir(str(self.worktree_dir))
        self.assertIsNotNone(common_git_dir)

        # Install hook with force to handle case where it already exists
        success, status = init_project.install_pre_push_hook(str(self.worktree_dir), force=True)

        self.assertTrue(success, f'Hook installation should succeed: {status}')

        # Verify hook is in the right place (common dir, not worktree)
        hook_path = common_git_dir / 'hooks' / 'pre-push'
        self.assertTrue(hook_path.exists(), f'Hook should be installed at {hook_path}')
        self.assertTrue(hook_path.is_file(), 'Hook should be a file')

    def test_install_hook_idempotent(self):
        """Test that hook installation is idempotent"""
        common_git_dir = init_project.resolve_real_git_dir(str(self.worktree_dir))
        hook_path = common_git_dir / 'hooks' / 'pre-push'

        # Remove existing hook if any
        if hook_path.exists():
            hook_path.unlink()

        # Install twice
        success1, status1 = init_project.install_pre_push_hook(str(self.worktree_dir))
        success2, status2 = init_project.install_pre_push_hook(str(self.worktree_dir))

        self.assertTrue(success1, f'First install should succeed: {status1}')
        self.assertFalse(success2, f'Second install should skip (already exists): {status2}')
        self.assertTrue(hook_path.exists(), 'Hook should exist after both attempts')


if __name__ == '__main__':
    unittest.main()
