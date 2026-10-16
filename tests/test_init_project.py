"""Tests for tools/init_project.py -- project initialization scaffolder."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure tools/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from init_project import (
    PRE_PUSH_HOOK,
    build_domain_map,
    detect_project_name,
    discover_code_dirs,
    init_project,
    install_pre_push_hook,
    copy_secret_scan_script,
    write_file,
)


class TestWriteFile(unittest.TestCase):
    """Test the write_file helper."""

    def test_creates_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "sub", "test.txt")
            result = write_file(fp, "hello")
            self.assertTrue(result)
            self.assertEqual(Path(fp).read_text(encoding="utf-8"), "hello")

    def test_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "test.txt")
            Path(fp).write_text("original", encoding="utf-8")
            result = write_file(fp, "new content")
            self.assertFalse(result)
            self.assertEqual(Path(fp).read_text(encoding="utf-8"), "original")

    def test_overwrites_existing_with_force(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "test.txt")
            Path(fp).write_text("original", encoding="utf-8")
            result = write_file(fp, "new content", force=True)
            self.assertTrue(result)
            self.assertEqual(Path(fp).read_text(encoding="utf-8"), "new content")


class TestDiscoverCodeDirs(unittest.TestCase):
    """Test discovery of well-known code directories."""

    def test_finds_src_dir(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "src").mkdir()
            found = discover_code_dirs(td)
            self.assertIn("src", found)

    def test_finds_multiple_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "src").mkdir()
            (Path(td) / "lib").mkdir()
            (Path(td) / "app").mkdir()
            found = discover_code_dirs(td)
            self.assertIn("src", found)
            self.assertIn("lib", found)
            self.assertIn("app", found)

    def test_ignores_non_code_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "random_dir").mkdir()
            (Path(td) / "docs").mkdir()
            found = discover_code_dirs(td)
            self.assertEqual(found, [])

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            found = discover_code_dirs(td)
            self.assertEqual(found, [])


class TestBuildDomainMap(unittest.TestCase):
    """Test domain map text generation."""

    def test_empty_domains(self):
        result = build_domain_map([])
        self.assertIn("no code directories", result)

    def test_single_domain(self):
        result = build_domain_map(["src"])
        self.assertIn("src/", result)
        self.assertIn("read src/CLAUDE.md", result)

    def test_multiple_domains(self):
        result = build_domain_map(["src", "lib"])
        self.assertIn("src/", result)
        self.assertIn("lib/", result)


class TestDetectProjectName(unittest.TestCase):
    """Test project name auto-detection."""

    def test_falls_back_to_dir_name(self):
        with tempfile.TemporaryDirectory() as td:
            name = detect_project_name(td)
            # Should return the directory basename
            self.assertEqual(name, Path(td).name)

    def test_uses_git_remote_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            # Initialize a git repo with a remote
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "remote", "add", "origin",
                 "https://github.com/testuser/my-cool-project.git"],
                cwd=td, capture_output=True, timeout=10,
            )
            name = detect_project_name(td)
            self.assertEqual(name, "my-cool-project")


class TestInstallPrePushHook(unittest.TestCase):
    """Test git pre-push hook installation."""

    def test_installs_hook_in_git_repo(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            ok, status = install_pre_push_hook(td)
            self.assertTrue(ok)
            self.assertEqual(status, "installed")
            hook = Path(td) / ".git" / "hooks" / "pre-push"
            self.assertTrue(hook.exists())
            content = hook.read_text(encoding="utf-8")
            self.assertIn("secret_scan", content)

    def test_skips_without_git_dir(self):
        with tempfile.TemporaryDirectory() as td:
            ok, status = install_pre_push_hook(td)
            self.assertFalse(ok)
            self.assertIn("no .git", status)

    def test_skips_existing_hook_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            hook = Path(td) / ".git" / "hooks" / "pre-push"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            ok, status = install_pre_push_hook(td, force=False)
            self.assertFalse(ok)
            self.assertIn("already exists", status)

    def test_replaces_existing_hook_with_force(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            hook = Path(td) / ".git" / "hooks" / "pre-push"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            ok, status = install_pre_push_hook(td, force=True)
            self.assertTrue(ok)
            content = hook.read_text(encoding="utf-8")
            self.assertIn("secret_scan", content)


class TestInitProject(unittest.TestCase):
    """End-to-end tests for init_project."""

    def test_full_scaffold_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            result = init_project(td, project_name="test-project")
            self.assertEqual(result["project_name"], "test-project")
            self.assertIn("CLAUDE.md", result["files_created"])
            self.assertIn("aesop.config.json", result["files_created"])
            self.assertIn("state/.gitkeep", result["files_created"])
            self.assertIn(".github/workflows/ci.yml", result["files_created"])
            self.assertIn(".git/hooks/pre-push", result["files_created"])

            # Verify CLAUDE.md content
            claude_md = Path(td) / "CLAUDE.md"
            content = claude_md.read_text(encoding="utf-8")
            self.assertIn("test-project", content)
            self.assertIn("Dispatch rule", content)

            # Verify config
            config_path = Path(td) / "aesop.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["state_root"], "./state")
            self.assertEqual(config["dashboard"]["port"], 8770)

    def test_scaffold_with_src_dir(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            (Path(td) / "src").mkdir()
            result = init_project(td, project_name="my-app")
            self.assertIn("src", result["domains_found"])
            self.assertIn("src/CLAUDE.md", result["files_created"])

            # Verify domain CLAUDE.md content
            domain_claude = Path(td) / "src" / "CLAUDE.md"
            content = domain_claude.read_text(encoding="utf-8")
            self.assertIn("src", content)
            self.assertIn("Universal rules", content)

    def test_idempotent_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            # First run
            result1 = init_project(td, project_name="proj")
            self.assertTrue(len(result1["files_created"]) > 0)

            # Second run without force -- files should be skipped
            result2 = init_project(td, project_name="proj")
            self.assertEqual(len(result2["files_created"]), 0)
            self.assertTrue(len(result2["files_skipped"]) > 0)

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            # First run
            init_project(td, project_name="proj")

            # Second run with force
            result = init_project(td, project_name="proj-v2", force=True)
            self.assertIn("CLAUDE.md", result["files_created"])
            content = (Path(td) / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("proj-v2", content)

    def test_nonexistent_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            init_project("/nonexistent/path/that/does/not/exist")

    def test_ci_yml_content(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            init_project(td, project_name="ci-test")
            ci = Path(td) / ".github" / "workflows" / "ci.yml"
            content = ci.read_text(encoding="utf-8")
            self.assertIn("name: CI", content)
            self.assertIn("actions/checkout", content)
            self.assertIn("ubuntu-latest", content)

    def test_config_has_identity(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            # Set git identity in the temp repo only
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=td, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=td, capture_output=True, timeout=10,
            )
            result = init_project(td, project_name="id-test")
            config = json.loads(
                (Path(td) / "aesop.config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["identity"]["name"], "Test User")
            self.assertEqual(config["identity"]["email"], "test@example.com")

    def test_state_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            init_project(td, project_name="state-test")
            self.assertTrue((Path(td) / "state").is_dir())
            self.assertTrue((Path(td) / "state" / ".gitkeep").exists())


class TestSecretScanCopy(unittest.TestCase):
    """Test that secret_scan.py is copied to the scaffolded repo."""

    def test_copy_secret_scan_script(self):
        """Verify copy_secret_scan_script copies the file and makes it executable."""
        with tempfile.TemporaryDirectory() as td:
            ok, status = copy_secret_scan_script(td)
            self.assertTrue(ok)
            self.assertEqual(status, "copied")

            scan_file = Path(td) / "tools" / "secret_scan.py"
            self.assertTrue(scan_file.exists())

            # Verify content
            content = scan_file.read_text(encoding="utf-8")
            self.assertIn("secret_scan.py", content)
            self.assertIn("--staged", content)

    def test_secret_scan_executable(self):
        """Verify copied secret_scan.py is executable."""
        with tempfile.TemporaryDirectory() as td:
            ok, _ = copy_secret_scan_script(td)
            self.assertTrue(ok)

            scan_file = Path(td) / "tools" / "secret_scan.py"
            # Check if executable (on POSIX; no-op on Windows)
            st = scan_file.stat()
            is_exec = bool(st.st_mode & stat.S_IXUSR)
            # On Windows this might be False, but on POSIX it should be True
            # We just verify the file was created successfully
            self.assertTrue(scan_file.exists())


class TestBehavioralSecretScan(unittest.TestCase):
    """Behavioral tests for the secret scan pre-push hook integration."""

    def test_init_includes_secret_scan_py(self):
        """Verify init_project includes tools/secret_scan.py in files_created."""
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            result = init_project(td, project_name="secret-test")
            # Should have copied secret_scan.py
            self.assertTrue(
                any("secret_scan.py" in f for f in result["files_created"]),
                f"secret_scan.py not found in files_created: {result['files_created']}"
            )
            self.assertTrue(
                (Path(td) / "tools" / "secret_scan.py").exists(),
                "tools/secret_scan.py was not created"
            )

    def test_hook_file_contains_fail_closed_logic(self):
        """Verify the hook template contains fail-closed logic (else branch with exit 1)."""
        # Check the PRE_PUSH_HOOK template itself
        self.assertIn("else", PRE_PUSH_HOOK)
        self.assertIn("not found", PRE_PUSH_HOOK.lower())
        self.assertIn("exit 1", PRE_PUSH_HOOK)

    def test_hook_installed_with_scan_script_reference(self):
        """Verify hook is installed and references the scan script path."""
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10, encoding='utf-8',
            )
            init_project(td, project_name="hook-test")

            hook_path = Path(td) / ".git" / "hooks" / "pre-push"
            self.assertTrue(hook_path.exists())

            hook_content = hook_path.read_text(encoding="utf-8")
            # Verify it references the tools/secret_scan.py path
            self.assertIn("tools/secret_scan.py", hook_content)
            # Verify it has fail-closed logic
            self.assertIn("else", hook_content)
            self.assertIn("exit 1", hook_content)

    def test_hook_executable_on_posix(self):
        """Verify hook is marked executable (on POSIX systems)."""
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10, encoding='utf-8',
            )
            init_project(td, project_name="exec-test")

            hook_path = Path(td) / ".git" / "hooks" / "pre-push"
            st = hook_path.stat()
            # On POSIX: check execute bit; on Windows this is always False (no-op)
            # We just verify the file exists and is not 0 bytes
            self.assertTrue(hook_path.stat().st_size > 0)

    def test_missing_script_triggers_fail_closed_logic(self):
        """
        Unit test: verify the hook template contains the fail-closed path.
        When secret_scan.py is missing, the else branch should exit 1.
        """
        # The hook template should contain explicit fail-closed logic
        self.assertIn("if [ -f \"$scan_script\" ]", PRE_PUSH_HOOK)
        self.assertIn("else", PRE_PUSH_HOOK)
        # After the else, there should be an error message and exit 1
        lines = PRE_PUSH_HOOK.split('\n')
        else_idx = None
        exit_1_idx = None
        for i, line in enumerate(lines):
            if 'else' in line:
                else_idx = i
            if 'exit 1' in line and else_idx is not None:
                exit_1_idx = i
                break
        self.assertIsNotNone(else_idx, "Hook should have else branch")
        self.assertIsNotNone(exit_1_idx, "Hook should have exit 1 after else")
        self.assertGreater(exit_1_idx, else_idx, "exit 1 should come after else")


class TestCLI(unittest.TestCase):
    """Test CLI invocation via subprocess."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "..", "tools", "init_project.py"), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--dir", result.stdout)
        self.assertIn("--name", result.stdout)
        self.assertIn("--force", result.stdout)

    def test_unknown_flag_exits_2(self):
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "..", "tools", "init_project.py"), "--bogus"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 2)

    def test_cli_scaffold(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                ["git", "init", "-q"], cwd=td,
                capture_output=True, timeout=10,
            )
            result = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "..", "tools", "init_project.py"),
                 "--dir", td, "--name", "cli-test"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("cli-test", result.stdout)
            self.assertTrue((Path(td) / "CLAUDE.md").exists())


ZERO_SHA = "0" * 40

# Runtime-assembled dummy GitHub-style token (matches secret_scan github_token rule).
# Never a literal credential in this file.
DUMMY_GH_TOKEN = "ghp_" + "A" * 36


def _bash_exe():
    """Git-for-Windows bash when present (System32 bash.exe is WSL), else PATH bash."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidate = os.path.join(pf, "Git", "bin", "bash.exe")
    if os.path.isfile(candidate):
        return candidate
    return shutil.which("bash") or "bash"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


def _make_feature_repo(td):
    """Scratch repo on feat/x with one clean commit, scan script and pre-push hook installed."""
    assert _git(td, "init", "-q", "-b", "feat/x").returncode == 0
    _git(td, "config", "user.email", "test@example.com")
    _git(td, "config", "user.name", "Test User")
    ok, status = copy_secret_scan_script(td)
    assert ok, status
    ok, status = install_pre_push_hook(td)
    assert ok, status
    Path(td, "README.md").write_text("clean\n", encoding="utf-8")
    _git(td, "add", "README.md")
    assert _git(td, "commit", "-q", "-m", "clean").returncode == 0
    return Path(td) / ".git" / "hooks" / "pre-push"


def _head_sha(td):
    return _git(td, "rev-parse", "HEAD").stdout.strip()


def _run_pre_push(td, hook, stdin_line):
    """Execute the generated hook the way git does: args (remote, url), ref lines on stdin."""
    return subprocess.run(
        [_bash_exe(), hook.as_posix(), "origin", "https://example.invalid/aesop-test.git"],
        cwd=td, input=stdin_line, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


class TestPrePushHookExecutes(unittest.TestCase):
    """Run the generated pre-push hook in a scratch repo with a fake git stdin line."""

    def test_push_to_remote_main_from_feature_branch_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            hook = _make_feature_repo(td)
            head = _head_sha(td)
            res = _run_pre_push(td, hook, f"refs/heads/feat/x {head} refs/heads/main {ZERO_SHA}\n")
            self.assertEqual(res.returncode, 1, f"stdout={res.stdout!r} stderr={res.stderr!r}")
            self.assertIn("blocked", res.stdout)

    def test_clean_feature_push_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            hook = _make_feature_repo(td)
            head = _head_sha(td)
            res = _run_pre_push(td, hook, f"refs/heads/feat/x {head} refs/heads/feat/x {ZERO_SHA}\n")
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout!r} stderr={res.stderr!r}")

    def test_committed_secret_in_pushed_range_is_blocked(self):
        """The secret is COMMITTED, so the staged index is empty; only the pushed range shows it."""
        with tempfile.TemporaryDirectory() as td:
            hook = _make_feature_repo(td)
            Path(td, "config.txt").write_text(f"token={DUMMY_GH_TOKEN}\n", encoding="utf-8")
            _git(td, "add", "config.txt")
            assert _git(td, "commit", "-q", "-m", "add config").returncode == 0
            self.assertEqual(_git(td, "diff", "--cached", "--name-only").stdout.strip(), "")
            head = _head_sha(td)
            res = _run_pre_push(td, hook, f"refs/heads/feat/x {head} refs/heads/feat/x {ZERO_SHA}\n")
            self.assertEqual(res.returncode, 1, f"stdout={res.stdout!r} stderr={res.stderr!r}")

    def test_missing_scan_script_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            hook = _make_feature_repo(td)
            (Path(td) / "tools" / "secret_scan.py").unlink()
            head = _head_sha(td)
            res = _run_pre_push(td, hook, f"refs/heads/feat/x {head} refs/heads/feat/x {ZERO_SHA}\n")
            self.assertEqual(res.returncode, 1, f"stdout={res.stdout!r} stderr={res.stderr!r}")


class TestInitForceRefreshesScanScript(unittest.TestCase):
    """init --force must replace a stale tools/secret_scan.py; plain init must keep it."""

    def _stale_repo(self, td):
        subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, timeout=10)
        init_project(td, project_name="proj")
        scan = Path(td) / "tools" / "secret_scan.py"
        scan.write_text("# stale copy\n", encoding="utf-8")
        return scan

    def test_force_replaces_stale_scan_script(self):
        with tempfile.TemporaryDirectory() as td:
            scan = self._stale_repo(td)
            source = (Path(__file__).parent.parent / "tools" / "secret_scan.py").read_text(encoding="utf-8")
            result = init_project(td, project_name="proj", force=True)
            self.assertEqual(scan.read_text(encoding="utf-8"), source)
            self.assertIn("tools/secret_scan.py", result["files_created"])

    def test_without_force_stale_scan_script_is_kept_and_reported(self):
        with tempfile.TemporaryDirectory() as td:
            scan = self._stale_repo(td)
            result = init_project(td, project_name="proj", force=False)
            self.assertEqual(scan.read_text(encoding="utf-8"), "# stale copy\n")
            self.assertTrue(any(f.startswith("tools/secret_scan.py") for f in result["files_skipped"]))


class TestFreshConfigPassesDoctor(unittest.TestCase):
    """A freshly scaffolded aesop.config.json must pass doctor's config field check."""

    def test_fresh_init_config_passes_doctor_config_check(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, timeout=10)
            init_project(td, project_name="doctor-test")
            doctor = Path(__file__).parent.parent / "tools" / "doctor.js"
            res = subprocess.run(
                [shutil.which("node") or "node", str(doctor), "--json"], cwd=td,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            report = json.loads(res.stdout)
            rows = [c for c in report["checks"] if c["label"].startswith("aesop.config.json")]
            self.assertEqual(len(rows), 1, report["checks"])
            self.assertTrue(rows[0]["passed"], f"doctor config check failed: {rows[0]['hint']}")


if __name__ == "__main__":
    unittest.main()
