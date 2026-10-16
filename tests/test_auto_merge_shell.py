"""Verify auto_merge.run() uses list-form subprocess (no shell=True)."""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import auto_merge  # noqa: E402


class TestAutoMergeNoShell(unittest.TestCase):
    def test_run_helper_uses_list_form(self):
        """The run() helper must not pass shell=True to subprocess."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess = False
            if isinstance(func, ast.Attribute) and func.attr == 'run':
                if isinstance(func.value, ast.Attribute):
                    is_subprocess = func.value.attr == 'subprocess'
                elif isinstance(func.value, ast.Name):
                    is_subprocess = func.value.id == 'subprocess'
            if not is_subprocess:
                continue

            for kw in node.keywords:
                if kw.arg == 'shell':
                    self.assertFalse(
                        isinstance(kw.value, ast.Constant) and kw.value.value is True,
                        f'subprocess.run() at line {node.lineno} uses shell=True'
                    )

    def test_run_callers_pass_lists(self):
        """Every call to the module-level run() must pass a list, not a string."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == 'run'):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            self.assertNotIsInstance(
                first_arg, (ast.Constant, ast.JoinedStr),
                f'run() at line {node.lineno} passes a string instead of a list'
            )


class TestAutoMergeTimeout(unittest.TestCase):
    """Test that run() helper has timeout protection."""

    def test_run_has_timeout_parameter(self):
        """The run() helper must accept a timeout parameter."""
        # After subprocess_common refactor, run() is in subprocess_common.py
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'subprocess_common.py')
        with open(src, encoding='utf-8') as f:
            content = f.read()

        # Parse and find the run() function definition
        tree = ast.parse(content, filename=src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != 'run':
                continue
            # Check that timeout is in parameters (positional or keyword-only)
            arg_names = [arg.arg for arg in node.args.args]
            kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            all_arg_names = arg_names + kwonly_names
            self.assertIn('timeout', all_arg_names,
                         'run() helper missing timeout parameter')
            return
        self.fail('Could not find run() function in subprocess_common.py')

    def test_run_subprocess_has_timeout(self):
        """The subprocess.run() call inside run() must pass a timeout."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'subprocess_common.py')
        with open(src, encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content, filename=src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != 'run':
                continue
            # Find subprocess.run call within this function
            for subnode in ast.walk(node):
                if not isinstance(subnode, ast.Call):
                    continue
                func = subnode.func
                is_subprocess_run = False
                if isinstance(func, ast.Attribute) and func.attr == 'run':
                    if isinstance(func.value, ast.Name):
                        is_subprocess_run = func.value.id == 'subprocess'
                if not is_subprocess_run:
                    continue
                # Found subprocess.run, verify it has timeout
                has_timeout = any(kw.arg == 'timeout' for kw in subnode.keywords)
                self.assertTrue(has_timeout,
                              'subprocess.run() in run() helper must have timeout parameter')
                return
            self.fail('Could not find subprocess.run() call in run() function')

    def test_subprocess_timeout_raises_exception(self):
        """Verify that subprocess timeout raises TimeoutExpired."""
        # Import the run function from subprocess_common (post-refactor)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        try:
            from subprocess_common import run
        finally:
            sys.path.pop(0)

        # Test with a command that will timeout: sleep longer than timeout
        # Use sys.executable to call python (portable across Windows/Linux)
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, '-c', 'import time; time.sleep(2)'],
                timeout=0.5)

    def test_timeout_not_swallowed(self):
        """Verify TimeoutExpired is not silently caught."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        try:
            from subprocess_common import run
        finally:
            sys.path.pop(0)

        # Try to verify the exception propagates, not caught internally
        try:
            run([sys.executable, '-c', 'import time; time.sleep(3)'],
                timeout=0.5, check=False)
            self.fail('Expected TimeoutExpired but no exception was raised')
        except subprocess.TimeoutExpired:
            # Expected: exception should propagate
            pass


class TestAutoMergeHelpEncoding(unittest.TestCase):
    """--help must not crash on a Windows cp1252 console (non-UTF-8 codepage)."""

    def test_help_runs_clean_under_cp1252(self):
        """python tools/auto_merge.py --help must exit 0 and print usage under cp1252."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        env = {**os.environ, 'PYTHONIOENCODING': 'cp1252'}
        result = subprocess.run(
            [sys.executable, src, '--help'],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f'--help crashed under cp1252 (stderr): {result.stderr}'
        )
        self.assertIn('usage', result.stdout)


def _git(cwd, *args):
    """Run git in a scratch repo and return stripped stdout; raise on non-zero exit."""
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', check=False)
    if r.returncode != 0:
        raise AssertionError(f'git {args} failed in {cwd}: {r.stderr}')
    return r.stdout.strip()


def _write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _commit(repo, msg):
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', msg)


class _ScratchRepoCase(unittest.TestCase):
    """Bare origin + primary clone on an unrelated branch with untracked WIP. Scratch only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='automerge-test-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.primary = os.path.join(self.tmp, 'primary')
        _git(self.tmp, 'init', '-q', '--bare', '-b', 'main', self.origin)
        _git(self.tmp, 'init', '-q', '-b', 'main', self.primary)
        _git(self.primary, 'config', 'user.name', 'Test')
        _git(self.primary, 'config', 'user.email', 'test@example.invalid')
        _git(self.primary, 'remote', 'add', 'origin', self.origin)
        _write(self.primary, 'README.md', 'base\n')
        # Seed the real secret gate (a copy, read-only use) so the gate runs in scratch.
        os.makedirs(os.path.join(self.primary, 'tools'), exist_ok=True)
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(auto_merge.__file__)), 'secret_scan.py'),
                        os.path.join(self.primary, 'tools', 'secret_scan.py'))
        _commit(self.primary, 'base')
        _git(self.primary, 'push', '-q', 'origin', 'main')
        _git(self.primary, 'checkout', '-q', '-b', 'pr')
        _write(self.primary, 'feature.txt', 'feature\n')
        _commit(self.primary, 'pr work')
        _git(self.primary, 'push', '-q', 'origin', 'pr')
        _git(self.primary, 'checkout', '-q', 'main')
        _write(self.primary, 'main-change.txt', 'main\n')
        _commit(self.primary, 'main moves ahead')
        _git(self.primary, 'push', '-q', 'origin', 'main')
        _git(self.primary, 'checkout', '-q', '-b', 'wip-local')
        _write(self.primary, 'wip.txt', 'untracked WIP\n')
        prev = os.getcwd()
        os.chdir(self.primary)
        self.addCleanup(os.chdir, prev)

    def origin_sha(self, branch):
        return _git(self.origin, 'rev-parse', f'refs/heads/{branch}')


class TestFixBranchLeavesCallerCheckoutAlone(_ScratchRepoCase):
    def test_wip_stays_untracked_and_head_unchanged(self):
        before_head = _git(self.primary, 'rev-parse', 'HEAD')
        before_branch = _git(self.primary, 'branch', '--show-current')
        old_pr = self.origin_sha('pr')
        ok, msg = auto_merge.fix_branch('pr')
        self.assertTrue(ok, msg)
        self.assertEqual(_git(self.primary, 'rev-parse', 'HEAD'), before_head)
        self.assertEqual(_git(self.primary, 'branch', '--show-current'), before_branch)
        self.assertIn('?? wip.txt', _git(self.primary, 'status', '--porcelain').splitlines())
        self.assertNotEqual(self.origin_sha('pr'), old_pr)
        self.assertEqual(_git(self.origin, 'show', 'refs/heads/pr:main-change.txt'), 'main')
        self.assertNotIn('wip.txt', _git(self.origin, 'ls-tree', '-r', '--name-only', 'refs/heads/pr').split())


class TestFixBranchSecretGate(_ScratchRepoCase):
    def test_secret_in_fix_diff_blocks_push(self):
        # Runtime-assembled dummy key: never a literal credential in this file.
        dummy = 'sk-' + 'Qw8Zr4Vt1Bn6Lk3Pm9Hs2Yc5Gd7Ja'
        _git(self.primary, 'checkout', '-q', 'main')
        _write(self.primary, 'notes/config.txt', f'token = {dummy}\n')
        _commit(self.primary, 'add config with token')
        _git(self.primary, 'push', '-q', 'origin', 'main')
        _git(self.primary, 'checkout', '-q', 'wip-local')
        old_pr = self.origin_sha('pr')
        ok, msg = auto_merge.fix_branch('pr')
        self.assertFalse(ok, msg)
        self.assertIn('secret', msg)
        self.assertEqual(self.origin_sha('pr'), old_pr)

    def test_zero_inspected_files_is_not_a_pass(self):
        fake = os.path.join(self.tmp, 'fake_scan.py')
        with open(fake, 'w', encoding='utf-8') as f:
            f.write("print('CLEAN: 0 files scanned')\n")
        orig = auto_merge.SECRET_SCAN
        auto_merge.SECRET_SCAN = fake
        self.addCleanup(setattr, auto_merge, 'SECRET_SCAN', orig)
        old_pr = self.origin_sha('pr')
        ok, msg = auto_merge.fix_branch('pr')
        self.assertFalse(ok, msg)
        self.assertIn('inspected 0 files', msg)
        self.assertEqual(self.origin_sha('pr'), old_pr)


if __name__ == '__main__':
    unittest.main()
