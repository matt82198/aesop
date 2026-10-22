#!/usr/bin/env python3
# secretscan: allow-pattern-docs
"""
Regression suite for the 2026-10-22 adversarial audit of tools/secret_scan.py and the
pre-push range scan. Every test drives the real scanner CLI against a throwaway git
repo or directory; credential-shaped values are synthetic and assembled at runtime so
no scanner pattern appears contiguously in this source file.

Fail-open paths closed here (audit item numbers in the test names):
  1  range mode scans every commit's blobs, not the net tree diff
  2  the clean-verdict cache is gone from the gate path (a hand-written cache blesses nothing)
  3  `NAME=/pw/tail` is a credential, only a closed regex literal is excused
  4  regex-shaped values that carry a high-entropy token still fire
  5  a placeholder word between hyphens does not excuse a real password
  6  a quoted DSN carrying `password=` in its query string fires
  8  a path scan that inspected zero files exits non-zero (NOTHING SCANNED)
  9  `--staged` on an empty index exits non-zero (EMPTY-INDEX) unless `--allow-empty`
  10 inherited GIT_DIR / GIT_WORK_TREE cannot redirect the scan to another repo
plus a table of the audit's FAIL-CLOSED probes, which must stay closed.

Run: python -m unittest tests.test_secret_scan_fail_closed_audit
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "tools" / "secret_scan.py"


def _j(*parts):
    """Join fragments at runtime (self-scan invariant, see module docstring)."""
    return "".join(parts)


PW = _j("Zx9kQp2L", "mN7vR4s")  # synthetic, mixed-case + digits, not a placeholder
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if extra:
        env.update(extra)
    return env


def _git(repo, *args, env=None):
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, env=env or _clean_env(),
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    return path


def _commit_file(repo, name, text, message):
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _scan(*args, cwd=None, env=None, stdin=None):
    result = subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env=env or _clean_env(), input=stdin,
    )
    return result.returncode, result.stdout, result.stderr


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_ss_audit_")).resolve()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = _clean_env({"AESOP_STATE_ROOT": str(self.state)})
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def scan_line(self, line, name="probe.py"):
        """Write `line` to a file in a fresh dir and path-scan that file."""
        target = self.tmp / "lines" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(line + "\n", encoding="utf-8")
        return _scan(str(target), env=self.env)


# --------------------------------------------------------------------------- 1
class TestRangeScansEveryCommit(_TmpCase):
    def _history(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "ok = 1\n", "base")
        _commit_file(repo, "leak.py", f'DB_PASSWORD = "{PW}"\n', "add secret")
        _git(repo, "rm", "-q", "leak.py")
        _git(repo, "commit", "-q", "-m", "remove secret")
        return repo, base, _git(repo, "rev-parse", "HEAD")

    def test_audit1_secret_added_then_removed_in_one_push_is_found(self):
        repo, base, tip = self._history()
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)
        self.assertIn("generic_credential_assignment", out)
        self.assertIn("leak.py", out)

    def test_audit1_secret_added_modified_clean_then_removed_is_found(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "ok = 1\n", "base")
        _commit_file(repo, "a.py", f'DB_PASSWORD = "{PW}"\n', "add")
        _commit_file(repo, "a.py", "x = 1\n", "scrub")
        tip = _git(repo, "rev-parse", "HEAD")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_audit1_clean_summary_counts_every_introduced_blob(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "ok = 1\n", "base")
        _commit_file(repo, "a.py", "a = 1\n", "a1")
        _commit_file(repo, "a.py", "a = 2\n", "a2")
        _commit_file(repo, "b.py", "b = 1\n", "b1")
        tip = _git(repo, "rev-parse", "HEAD")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("CLEAN: 3 files scanned", out)

    def test_audit1_secret_on_merged_side_branch_is_found(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "ok = 1\n", "base")
        _git(repo, "checkout", "-q", "-b", "side")
        _commit_file(repo, "s.env", f"API_TOKEN={PW}\n", "side secret")
        _git(repo, "checkout", "-q", "-b", "merged", base)
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        tip = _git(repo, "rev-parse", "HEAD")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_audit1_root_commit_in_range_is_scanned(self):
        repo = _init_repo(self.tmp / "repo")
        tip = _commit_file(repo, "leak.py", f'DB_PASSWORD = "{PW}"\n', "root")
        code, out, _ = _scan("--range", f"{EMPTY_TREE}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_audit1_secret_outside_range_base_is_not_reported(self):
        repo = _init_repo(self.tmp / "repo")
        _commit_file(repo, "old.py", f'DB_PASSWORD = "{PW}"\n', "old secret, already pushed")
        base = _git(repo, "rev-parse", "HEAD")
        tip = _commit_file(repo, "new.py", "n = 1\n", "new")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("CLEAN: 1 files scanned", out)

    def test_range_unresolvable_base_fails_closed(self):
        repo, _base, tip = self._history()
        code, out, err = _scan("--range", f"{'d' * 40}..{tip}", "--repo", str(repo), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertNotIn("CLEAN", out)
        self.assertIn("Failing CLOSED", err)

    def test_range_zero_base_fails_closed(self):
        repo, _base, tip = self._history()
        code, out, _ = _scan("--range", f"{'0' * 40}..{tip}", "--repo", str(repo), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertNotIn("CLEAN", out)

    def test_range_empty_range_is_clean(self):
        repo, _base, tip = self._history()
        code, out, _ = _scan("--range", f"{tip}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 0, out)

    def test_range_committed_blob_wins_over_scrubbed_working_copy(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "ok = 1\n", "base")
        tip = _commit_file(repo, "p.py", f'DB_PASSWORD = "{PW}"\n', "secret")
        (repo / "p.py").write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)


# --------------------------------------------------------------------------- 2
class TestCleanCacheRemoved(_TmpCase):
    def _poison(self, target):
        src = SCANNER.read_bytes()
        tag = hashlib.sha256(b"schema=1\n" + src).hexdigest()
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        cache = self.state / "cache" / "secret_scan"
        cache.mkdir(parents=True)
        entries = {str(target.resolve()): {"sha": sha, "notices": []}}
        (cache / "clean.json").write_text(
            json.dumps({"schema": 1, "tag": tag, "entries": entries}), encoding="utf-8")

    def test_audit2_hand_written_clean_cache_does_not_bless_a_secret(self):
        target = self.tmp / "work" / "p.py"
        target.parent.mkdir()
        target.write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        self._poison(target)
        code, out, _ = _scan(str(target), env=self.env)
        self.assertEqual(code, 1, out)
        self.assertIn("generic_credential_assignment", out)

    def test_audit2_poisoned_cache_in_temp_default_dir_is_ignored(self):
        """Same poisoning with AESOP_STATE_ROOT unset: the %TEMP% fallback is not read either."""
        target = self.tmp / "work" / "p.py"
        target.parent.mkdir()
        target.write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        fake_tmp = self.tmp / "faketmp"
        fake_tmp.mkdir()
        src = SCANNER.read_bytes()
        tag = hashlib.sha256(b"schema=1\n" + src).hexdigest()
        cache = fake_tmp / "cache" / "secret_scan"
        cache.mkdir(parents=True)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        (cache / "clean.json").write_text(json.dumps(
            {"schema": 1, "tag": tag, "entries": {str(target.resolve()): {"sha": sha, "notices": []}}}),
            encoding="utf-8")
        env = _clean_env({"TMP": str(fake_tmp), "TEMP": str(fake_tmp), "TMPDIR": str(fake_tmp)})
        env.pop("AESOP_STATE_ROOT", None)
        code, out, _ = _scan(str(target), env=env)
        self.assertEqual(code, 1, out)

    def test_audit2_clean_scan_writes_no_cache_file(self):
        target = self.tmp / "work" / "ok.py"
        target.parent.mkdir()
        target.write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan(str(target), env=self.env)
        self.assertEqual(code, 0, out)
        self.assertFalse((self.state / "cache").exists(), "scanner must not write a verdict cache")

    def test_no_cache_flag_is_still_accepted(self):
        target = self.tmp / "work" / "ok.py"
        target.parent.mkdir()
        target.write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan("--no-cache", str(target), env=self.env)
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- 3
class TestSlashValueExclusion(_TmpCase):
    def test_audit3_slash_prefix_with_tail_is_a_credential(self):
        code, out, _ = self.scan_line(f"DB_PASSWORD=/{PW}/tail")
        self.assertEqual(code, 1, out)
        self.assertIn("generic_credential_assignment", out)

    def test_audit3_slash_prefix_with_digit_tail_is_a_credential(self):
        code, out, _ = self.scan_line(f"DB_PASSWORD=/{PW}/v2")
        self.assertEqual(code, 1, out)

    def test_audit3_closed_regex_literal_with_flags_stays_excused(self):
        code, out, _ = self.scan_line(_j("SUPPRESSED_", "CRED = /", "tokenvalue", "123456", "/gi;"))
        self.assertEqual(code, 0, out)

    def test_audit3_closed_regex_literal_without_flags_stays_excused(self):
        code, out, _ = self.scan_line(_j("SUPPRESSED_", "CRED = /", "tokenvalue", "123456", "/;"))
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- 4
class TestRegexShapeExclusion(_TmpCase):
    def test_audit4_quoted_secret_with_digit_class_suffix_is_flagged(self):
        code, out, _ = self.scan_line(_j('DB_PASSWORD = "', PW, '\\d+"'))
        self.assertEqual(code, 1, out)

    def test_audit4_quoted_secret_with_group_and_star_is_flagged(self):
        code, out, _ = self.scan_line(_j('DB_PASSWORD = "', PW, '(?:x)*"'))
        self.assertEqual(code, 1, out)

    def test_audit4_quoted_secret_with_negated_class_is_flagged(self):
        code, out, _ = self.scan_line(_j('DB_PASSWORD = "', PW, '[^a]+"'))
        self.assertEqual(code, 1, out)

    def test_audit4_unquoted_secret_with_space_class_is_flagged(self):
        code, out, _ = self.scan_line(_j("DB_PASSWORD=", PW, "\\s*"))
        self.assertEqual(code, 1, out)

    def test_audit4_scanner_pattern_table_literal_stays_clean(self):
        """Control: a real rule-table literal (no high-entropy token) is still excused."""
        code, out, _ = self.scan_line(_j('TOKEN_KEY = "(?i)token', r'\s*[:=]\s*\S{8,}', '"'))
        self.assertEqual(code, 0, out)

    def test_audit4_anchored_pattern_with_only_words_stays_clean(self):
        code, out, _ = self.scan_line(_j('TOKEN_KEY = "^password', r'\s*=\s*\S+$', '"'))
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- 5
class TestPlaceholderWholeToken(_TmpCase):
    def test_audit5_dsn_password_with_test_between_hyphens_is_flagged(self):
        code, out, _ = self.scan_line(_j('DSN = "postgres://svc:Zx9-test-Qm2L', '@db.prod.corp/app"'))
        self.assertEqual(code, 1, out)
        self.assertIn("connection_string", out)

    def test_audit5_dsn_password_with_demo_between_hyphens_is_flagged(self):
        code, out, _ = self.scan_line(_j('DSN = "postgres://svc:Summer-demo-2024x', '@db.prod.corp/app"'))
        self.assertEqual(code, 1, out)

    def test_audit5_credential_with_test_between_hyphens_is_flagged(self):
        code, out, _ = self.scan_line(_j("DB_PASSWORD=Zx9-test-Qm2L"))
        self.assertEqual(code, 1, out)

    def test_audit5_credential_with_xxx_between_hyphens_is_flagged(self):
        code, out, _ = self.scan_line(_j("DB_PASSWORD=Zx9-xxx-Qm2L"))
        self.assertEqual(code, 1, out)

    def test_audit5_whole_placeholder_password_stays_clean(self):
        code, out, _ = self.scan_line(_j('DSN = "postgres://svc:changeme', '@db.prod.corp/app"'))
        self.assertEqual(code, 0, out)

    def test_audit5_plain_word_placeholder_compound_stays_clean(self):
        code, out, _ = self.scan_line("DB_PASSWORD=changeme-prod-1234")
        self.assertEqual(code, 0, out)

    def test_audit5_plain_test_password_compound_stays_clean(self):
        code, out, _ = self.scan_line(_j('DSN = "postgres://svc:test-password-1', '@db.prod.corp/app"'))
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- 6
class TestDsnQueryPassword(_TmpCase):
    def test_audit6_double_quoted_dsn_query_password_is_flagged(self):
        code, out, _ = self.scan_line(_j('DSN = "postgres://db.prod.corp/app?user=app&pass', 'word=', PW, '"'))
        self.assertEqual(code, 1, out)

    def test_audit6_single_quoted_dsn_query_pwd_is_flagged(self):
        code, out, _ = self.scan_line(_j("DSN = 'mysql://db.prod.corp/app?pwd=", PW, "&ssl=1'"))
        self.assertEqual(code, 1, out)

    def test_audit6_passwd_key_is_flagged(self):
        code, out, _ = self.scan_line(_j('u = "https://h.prod.corp/x?a=1&pass', 'wd=', PW, '"'))
        self.assertEqual(code, 1, out)

    def test_audit6_placeholder_query_password_stays_clean(self):
        code, out, _ = self.scan_line('DSN = "postgres://db.prod.corp/app?user=app&password=changeme"')
        self.assertEqual(code, 0, out)

    def test_audit6_template_query_password_stays_clean(self):
        code, out, _ = self.scan_line('DSN = "postgres://db.prod.corp/app?password=${DB_PW_VALUE}"')
        self.assertEqual(code, 0, out)

    def test_audit6_short_query_value_stays_clean(self):
        code, out, _ = self.scan_line('u = "https://h.prod.corp/x?password=1"')
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- 8
class TestZeroFilePathScan(_TmpCase):
    def test_audit8_empty_directory_exits_nonzero_nothing_scanned(self):
        d = self.tmp / "emptydir"
        d.mkdir()
        code, out, err = _scan(str(d), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertIn("NOTHING SCANNED", out + err)
        self.assertNotIn("CLEAN", out)

    def test_audit8_all_gitignored_directory_exits_nonzero(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / ".gitignore").write_text("ign/\n", encoding="utf-8")
        (repo / "ign").mkdir()
        (repo / "ign" / "s.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        code, out, err = _scan(str(repo / "ign"), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertIn("NOTHING SCANNED", out + err)

    def test_audit8_node_modules_only_directory_exits_nonzero(self):
        d = self.tmp / "nm" / "node_modules" / "x"
        d.mkdir(parents=True)
        (d / "c.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        code, out, err = _scan(str(self.tmp / "nm" / "node_modules"), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertIn("NOTHING SCANNED", out + err)
        self.assertNotIn("CLEAN", out)

    def test_audit8_from_stdin_with_no_paths_exits_nonzero(self):
        code, out, err = _scan("--from-stdin", env=self.env, stdin="")
        self.assertNotEqual(code, 0)
        self.assertIn("NOTHING SCANNED", out + err)

    def test_audit8_nonempty_directory_still_clean(self):
        d = self.tmp / "dir"
        d.mkdir()
        (d / "ok.py").write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan(str(d), env=self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("CLEAN: 1 files scanned", out)


# --------------------------------------------------------------------------- 9
class TestEmptyIndex(_TmpCase):
    def test_audit9_staged_with_empty_index_exits_nonzero(self):
        repo = _init_repo(self.tmp / "repo")
        _commit_file(repo, "ok.py", "x = 1\n", "base")
        code, out, err = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertIn("EMPTY-INDEX", out + err)
        self.assertNotIn("CLEAN", out)

    def test_audit9_allow_empty_flag_is_the_explicit_override(self):
        repo = _init_repo(self.tmp / "repo")
        _commit_file(repo, "ok.py", "x = 1\n", "base")
        code, out, _ = _scan("--staged", "--allow-empty", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 0, out)

    def test_audit9_staged_clean_file_still_passes(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / "n.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "n.py")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("CLEAN: 1 files scanned", out)

    def test_audit9_staged_secret_still_found(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / "n.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(repo, "add", "n.py")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_audit9_pre_push_hook_never_passes_allow_empty(self):
        """The gate must not carry the override: it is for non-hook callers only."""
        hook = (REPO_ROOT / "hooks" / "pre-push-policy.sh").read_text(encoding="utf-8")
        init = (REPO_ROOT / "tools" / "init_project.py").read_text(encoding="utf-8")
        self.assertNotIn("--allow-empty", hook.split("check_secret_scan()", 1)[1].split("check_tracker_guard()", 1)[0])
        self.assertNotIn("secret_scan.py\" --allow-empty", init)


# -------------------------------------------------------------------------- 10
class TestInheritedGitDirScrubbed(_TmpCase):
    def _two_repos(self):
        target = _init_repo(self.tmp / "target")
        (target / "s.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(target, "add", "s.py")
        other = _init_repo(self.tmp / "other")
        (other / "c.py").write_text("x = 1\n", encoding="utf-8")
        _git(other, "add", "c.py")
        return target, other

    def test_audit10_git_dir_pointing_at_clean_repo_cannot_hide_staged_secret(self):
        target, other = self._two_repos()
        env = dict(self.env, GIT_DIR=str(other / ".git"))
        code, out, _ = _scan("--staged", "--repo", str(target), env=env)
        self.assertEqual(code, 1, out)
        self.assertIn("s.py", out)

    def test_audit10_git_work_tree_cannot_redirect_staged_scan(self):
        target, other = self._two_repos()
        env = dict(self.env, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
        code, out, _ = _scan("--staged", "--repo", str(target), env=env)
        self.assertEqual(code, 1, out)

    def test_audit10_git_dir_cannot_redirect_range_scan(self):
        target = _init_repo(self.tmp / "target")
        base = _commit_file(target, "ok.py", "x = 1\n", "base")
        tip = _commit_file(target, "s.py", f'DB_PASSWORD = "{PW}"\n', "secret")
        other = _init_repo(self.tmp / "other")
        _commit_file(other, "c.py", "x = 1\n", "o")
        env = dict(self.env, GIT_DIR=str(other / ".git"))
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(target), env=env)
        self.assertEqual(code, 1, out)


# --------------------------------------------------------- FAIL-CLOSED probes
class TestAuditFailClosedProbesStayClosed(_TmpCase):
    """The audit's probes that were already closed. Each must still exit non-zero."""

    LINES = [
        ("plain quoted", "a.py", _j('DB_PASSWORD = "', PW, '"')),
        ("plain unquoted yaml", "a.yaml", _j("DB_PASSWORD: ", PW)),
        ("plain unquoted eq", "a.sh", _j("API_TOKEN=", PW)),
        ("regex literal both delims, secret rule", "b.js", _j("TOKEN = /", PW, "/gi")),
        ("slash prefix only", "b2.py", _j("DB_PASSWORD=/", PW)),
        ("trailing slash only", "b3.py", _j("DB_PASSWORD=", PW, "/")),
        ("aws secret base64 with slash", "b4.py", _j("AWS_SECRET_", "ACCESS_KEY=/wJalrXUtnFEMI/K7MDENGbPxRfiCYzzzzzz")),
        ("slash secret in quotes", "b5.py", _j('DB_PASSWORD = "/', PW, '/tail"')),
        ("quoted anchored", "c.py", _j('TOK', 'EN = "^', PW, '$"')),
        ("char class secret inside", "d5.py", _j('DB_PASSWORD = "[', PW, ']{8,}"')),
        ("secret next to regex shape, no quantifier", "d6.py", _j('DB_PASSWORD = "', PW, '\\d"')),
        ("stripe live key", "e.py", _j('STRIPE_SECRET_KEY = "sk_live_', "51Hq3vKd8Zp2Lm9X", '"')),
        ("bare dsn query password", "f2.txt", _j("postgres://db.prod.corp/app?user=app&password=", PW)),
        ("dsn userinfo password", "g.py", _j('DSN = "postgres://svc:', PW, '@db.prod.corp/test"')),
        ("dsn percent-encoded password", "g4.py", _j('DSN = "postgres://svc:Zx9%2Fk%40Qp2', '@db.prod.corp/app"')),
        ("dsn empty password then real", "g5.py",
         _j('A="postgres://svc:@db.prod.corp/app" B="postgres://svc:', PW, '@db.prod.corp/test"')),
        ("dsn db path is test", "g6.py", _j('DSN = "mysql://root:', PW, '@10.0.0.5/test"')),
    ]

    def test_closed_line_probes(self):
        for label, name, line in self.LINES:
            with self.subTest(label):
                code, out, _ = self.scan_line(line, name=name)
                self.assertEqual(code, 1, f"{label}: {out}")

    def test_nonexistent_path_exits_2(self):
        code, out, err = _scan(str(self.tmp / "nope.py"), env=self.env)
        self.assertEqual(code, 2, out + err)
        self.assertIn("Failing CLOSED", err)

    def test_one_good_one_bad_path_exits_2(self):
        good = self.tmp / "ok.py"
        good.write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan(str(good), str(self.tmp / "nope.py"), env=self.env)
        self.assertEqual(code, 2, out)

    def test_from_stdin_nonexistent_exits_2(self):
        code, out, _ = _scan("--from-stdin", env=self.env, stdin=str(self.tmp / "nope.py") + "\n")
        self.assertEqual(code, 2, out)

    def test_explicit_gitignored_file_is_still_scanned(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / ".gitignore").write_text("ign/\n", encoding="utf-8")
        (repo / "ign").mkdir()
        target = repo / "ign" / "s.py"
        target.write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        code, out, _ = _scan(str(target), env=self.env)
        self.assertEqual(code, 1, out)

    def test_tracked_node_modules_secret_found_by_range(self):
        repo = _init_repo(self.tmp / "repo")
        base = _commit_file(repo, "ok.py", "x = 1\n", "base")
        tip = _commit_file(repo, "node_modules/x/c.py", f'DB_PASSWORD = "{PW}"\n', "vendored")
        code, out, _ = _scan("--range", f"{base}..{tip}", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_staged_repo_flag_from_foreign_cwd_finds_secret(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / "s.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(repo, "add", "s.py")
        foreign = self.tmp / "foreign"
        foreign.mkdir()
        code, out, _ = _scan("--staged", "--repo", str(repo), cwd=foreign, env=self.env)
        self.assertEqual(code, 1, out)

    def test_staged_non_repo_fails_closed(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, _ = _scan("--staged", "--repo", str(plain), env=self.env)
        self.assertNotEqual(code, 0)
        self.assertNotIn("CLEAN", out)

    def test_staged_blob_wins_over_scrubbed_working_copy(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / "s.py").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(repo, "add", "s.py")
        (repo / "s.py").write_text("x = 1\n", encoding="utf-8")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_staged_rename_with_secret_found(self):
        repo = _init_repo(self.tmp / "repo")
        _commit_file(repo, "ok.py", "ok = 1\n", "base")
        _git(repo, "mv", "ok.py", "renamed.py")
        with open(repo / "renamed.py", "a", encoding="utf-8") as handle:
            handle.write(f'DB_PASSWORD = "{PW}"\n')
        _git(repo, "add", "renamed.py")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_staged_non_ascii_name_found(self):
        repo = _init_repo(self.tmp / "repo")
        name = "café.py"
        (repo / name).write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(repo, "add", "-A")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)

    def test_staged_textconv_cannot_blank_blob(self):
        repo = _init_repo(self.tmp / "repo")
        (repo / ".gitattributes").write_text("secret.dat diff=blank\n", encoding="utf-8")
        (repo / "secret.dat").write_text(f'DB_PASSWORD = "{PW}"\n', encoding="utf-8")
        _git(repo, "config", "diff.blank.textconv", "true")
        _git(repo, "add", "-A")
        code, out, _ = _scan("--staged", "--repo", str(repo), env=self.env)
        self.assertEqual(code, 1, out)


if __name__ == "__main__":
    unittest.main()
