#!/usr/bin/env python3
"""Behavioral portability tests for macOS and Bash 3.2 shell scripts.

Exercises the real shell implementations under simulated macOS/Bash 3.2
conditions:
1. Audit lock PID resolution (shell_pid / acquire_audit_lock / release_audit_lock)
   when BASHPID is unset/unavailable.
2. Bounded timeout execution (run_bounded_timeout in daemons/backup-fleet.sh)
   when GNU timeout and gtimeout are absent/unusable while Python is present.
3. Audit log verification without sha256sum (shasum -a 256 fallback),
   BSD stat -f %m fallback in tools/run_shell_tests.sh, and BSD-strict sed -i
   rejection across hooks/pre-push-policy.sh --test and
   tests/test_reconstitute_fixes.sh.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "pre-push-policy.sh"
BACKUP_FLEET_SCRIPT = REPO_ROOT / "daemons" / "backup-fleet.sh"
RUN_SHELL_TESTS_SCRIPT = REPO_ROOT / "tools" / "run_shell_tests.sh"
TEST_PRE_PUSH_SCRIPT = REPO_ROOT / "tests" / "test_pre_push_policy.sh"
TEST_RECONSTITUTE_FIXES = REPO_ROOT / "tests" / "test_reconstitute_fixes.sh"

_GIT_BASH_WIN = r"C:\Program Files\Git\bin\bash.exe"


def _bash() -> str:
    """Return a POSIX bash executable path, preferring Git Bash on Windows."""
    if sys.platform == "win32":
        if os.path.exists(_GIT_BASH_WIN):
            return _GIT_BASH_WIN
        found = shutil.which("bash")
        if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
            return found
        raise AssertionError("No Git Bash found for shell portability tests")
    return shutil.which("bash") or "bash"


def _posix_path(path: Path) -> str:
    """Convert a Path to a POSIX-friendly path string for Git Bash."""
    s = str(path).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        return f"/{drive}{s[2:]}"
    return s


class TestMacosShellPortability(unittest.TestCase):
    """Behavioral tests for macOS / Bash 3.2 portability."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="macos_port_")
        self.tmp = Path(self.temp_dir.name)
        self.home_dir = self.tmp / "home"
        self.tmp_sub = self.tmp / "tmp"
        self.shadow_bin = self.tmp / "shadow_bin"
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_sub.mkdir(parents=True, exist_ok=True)
        self.shadow_bin.mkdir(parents=True, exist_ok=True)
        self.bash = _bash()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _base_env(self, extra_path_dir: Path | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = _posix_path(self.home_dir)
        env["USERPROFILE"] = str(self.home_dir)
        env["TMPDIR"] = _posix_path(self.tmp_sub)
        env["TMP"] = _posix_path(self.tmp_sub)
        env["TEMP"] = _posix_path(self.tmp_sub)
        env.pop("AESOP_ROOT", None)
        env.pop("AESOP_STATE_ROOT", None)
        env.pop("CONDUCTOR_ROOT", None)
        if extra_path_dir is not None:
            sep = os.pathsep
            env["PATH"] = f"{extra_path_dir}{sep}{env.get('PATH', '')}"
        return env

    def _run_bash(self, script: str, env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.bash, "-c", script],
            cwd=str(self.tmp),
            env=env or self._base_env(),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def _write_exe(self, name: str, content: str) -> Path:
        p = self.shadow_bin / name
        p.write_text(content, encoding="utf-8", newline="\n")
        os.chmod(p, 0o755)
        return p

    def test_shell_pid_and_audit_lock_under_simulated_bash32(self):
        """Audit lock PID must resolve to distinct non-empty numeric PIDs when BASHPID is unset."""
        hook_posix = _posix_path(HOOK_SCRIPT)
        lock_dir = _posix_path(self.tmp / "state" / ".audit-log-lock")
        script = f"""
set -u
source "{hook_posix}"
mkdir -p "$(dirname "{lock_dir}")"

# Subshell 1 with BASHPID unset (simulating macOS /bin/bash 3.2)
pid1=$(
  unset BASHPID
  shell_pid p1
  printf '%s' "$p1"
) || exit 11

# Subshell 2 with BASHPID unset
pid2=$(
  unset BASHPID
  shell_pid p2
  printf '%s' "$p2"
) || exit 12

printf 'PIDS=%s,%s\\n' "$pid1" "$pid2"

# Acquire lock in subshell A with BASHPID unset, verify owner PID written and
# verify a second subshell B cannot release subshell A's lock.
(
  unset BASHPID
  acquire_audit_lock "{lock_dir}" || exit 21
  owner=$(audit_lock_owner "{lock_dir}")
  printf 'OWNER=%s\\n' "$owner"
  (
    unset BASHPID
    release_audit_lock "{lock_dir}"
  )
  if [ ! -d "{lock_dir}" ]; then
    printf 'ERR=non_owner_released_lock\\n'
    exit 22
  fi
  release_audit_lock "{lock_dir}"
  if [ -d "{lock_dir}" ]; then
    printf 'ERR=owner_failed_to_release_lock\\n'
    exit 23
  fi
)
"""
        res = self._run_bash(script)
        combined = res.stdout + "\n" + res.stderr
        self.assertEqual(res.returncode, 0, f"Bash 3.2 lock test failed (rc={res.returncode}):\n{combined}")

        pids_line = next((l for l in res.stdout.splitlines() if l.startswith("PIDS=")), "")
        self.assertTrue(pids_line, f"Missing PIDS line in stdout:\n{combined}")
        p1, p2 = pids_line.split("=", 1)[1].split(",", 1)
        self.assertTrue(p1.isdigit() and int(p1) > 0, f"Invalid pid1={p1!r}")
        self.assertTrue(p2.isdigit() and int(p2) > 0, f"Invalid pid2={p2!r}")
        self.assertNotEqual(p1, p2, "Two subshells must receive distinct PIDs when BASHPID is unset")

        owner_line = next((l for l in res.stdout.splitlines() if l.startswith("OWNER=")), "")
        self.assertTrue(owner_line, f"Missing OWNER line in stdout:\n{combined}")
        owner = owner_line.split("=", 1)[1]
        self.assertTrue(owner.isdigit() and int(owner) > 0, f"Invalid lock owner PID={owner!r}")

    def test_run_bounded_timeout_when_gnu_timeout_absent(self):
        """run_bounded_timeout falls back to Python when timeout/gtimeout are unavailable."""
        # Shadow timeout and gtimeout with failing stubs (simulating macOS without coreutils)
        self._write_exe("timeout", "#!/bin/sh\necho 'timeout: command not found' >&2\nexit 127\n")
        self._write_exe("gtimeout", "#!/bin/sh\necho 'gtimeout: command not found' >&2\nexit 127\n")

        backup_posix = _posix_path(BACKUP_FLEET_SCRIPT)
        aesop_fixture = _posix_path(self.tmp / "aesop-fixture")
        env = self._base_env(extra_path_dir=self.shadow_bin)

        fast_script = f"""
export AESOP_ROOT="{aesop_fixture}"
mkdir -p "$AESOP_ROOT/state"
source "{backup_posix}"
run_bounded_timeout 5 echo fast_command_ok
"""
        fast_res = self._run_bash(fast_script, env=env, timeout=20)
        self.assertEqual(
            fast_res.returncode,
            0,
            f"Fast command failed under run_bounded_timeout:\n{fast_res.stdout}\n{fast_res.stderr}",
        )
        self.assertIn("fast_command_ok", fast_res.stdout)

        slow_script = f"""
export AESOP_ROOT="{aesop_fixture}"
mkdir -p "$AESOP_ROOT/state"
source "{backup_posix}"
run_bounded_timeout 1 sleep 5
"""
        t0 = time.monotonic()
        slow_res = self._run_bash(slow_script, env=env, timeout=20)
        elapsed = time.monotonic() - t0
        self.assertEqual(
            slow_res.returncode,
            124,
            f"Expected exit 124 on timeout, got {slow_res.returncode}:\n{slow_res.stdout}\n{slow_res.stderr}",
        )
        self.assertLess(elapsed, 4.5, f"Timeout took too long ({elapsed:.2f}s)")

    def test_live_hb_snapshot_bsd_stat_fallback(self):
        """live_hb_snapshot falls back to BSD stat -f %m when GNU stat -c %Y fails."""
        runner_posix = _posix_path(RUN_SHELL_TESTS_SCRIPT)
        hb_file = self.tmp / "hb"
        hb_file.write_text("1700000123\n", encoding="utf-8", newline="\n")
        hb_posix = _posix_path(hb_file)

        script = f"""
set -u
source "{runner_posix}"
stat() {{
  if [ "${{1:-}}" = "-c" ]; then
    return 1
  fi
  if [ "${{1:-}}" = "-f" ] && [ "${{2:-}}" = "%m" ]; then
    printf '1700000123\\n'
    return 0
  fi
  return 1
}}
live_hb_snapshot "{hb_posix}"
"""
        res = self._run_bash(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "1700000123|1700000123")

    def test_audit_log_shasum_only_and_no_unportable_sed_i(self):
        """Audit verification works with shasum only, and shell tests avoid GNU-only sed -i."""
        # Shadow sha256sum with failing stub and provide a portable shasum shim
        self._write_exe("sha256sum", "#!/bin/sh\nexit 127\n")
        py_exe = _posix_path(Path(sys.executable))
        self._write_exe(
            "shasum",
            f"""#!/bin/sh
if [ "${{1:-}}" = "-a" ] && [ "${{2:-}}" = "256" ]; then
  shift 2
fi
exec "{py_exe}" -c 'import hashlib,sys; data=sys.stdin.buffer.read() if not sys.argv[1:] else open(sys.argv[1],"rb").read(); print(hashlib.sha256(data).hexdigest() + "  -")' "$@"
""",
        )

        # BSD-strict sed wrapper: fails if -i is given without a separate backup extension
        real_sed = shutil.which("sed") or "/usr/bin/sed"
        if sys.platform == "win32" and os.path.exists(r"C:\Program Files\Git\usr\bin\sed.exe"):
            real_sed = "/usr/bin/sed"
        self._write_exe(
            "sed",
            f"""#!/bin/sh
prev=""
for arg in "$@"; do
  if [ "$prev" = "-i" ]; then
    case "$arg" in
      ''|*.bak) ;;
      */*|*s/*|*[0-9]s*)
        echo "sed: 1: \\"$arg\\": invalid command code (BSD sed requires -i '')" >&2
        exit 1
        ;;
    esac
  fi
  prev="$arg"
done
exec "{real_sed}" "$@"
""",
        )

        env = self._base_env(extra_path_dir=self.shadow_bin)
        hook_posix = _posix_path(HOOK_SCRIPT)
        aesop_state_root = _posix_path(self.tmp / "audit_aesop")

        # 1) Verify log_block + verify_audit_log work when sha256sum is absent and only shasum exists
        audit_script = f"""
set -u
source "{hook_posix}"
command() {{
  if [ "${{1:-}}" = "-v" ] && [ "${{2:-}}" = "sha256sum" ]; then
    return 1
  fi
  builtin command "$@"
}}
export AESOP_ROOT="{aesop_state_root}"
mkdir -p "$AESOP_ROOT/state"
log_block "entry_1" || exit 1
log_block "entry_2" || exit 2
verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log"
"""
        audit_res = self._run_bash(audit_script, env=env)
        self.assertEqual(
            audit_res.returncode,
            0,
            f"Audit log failed with shasum-only environment:\n{audit_res.stdout}\n{audit_res.stderr}",
        )
        self.assertIn("Audit log verification OK (2 entries)", audit_res.stdout)

        # 2) Verify tamper-detection test blocks in hooks/pre-push-policy.sh and
        # tests/test_pre_push_policy.sh do not use GNU-only `sed -i 'expr' file`
        # and verify test_reconstitute_fixes.sh Test 2.2 runs cleanly under BSD-strict sed
        # and without GNU `timeout`.
        self._write_exe("timeout", "#!/bin/sh\necho 'timeout: command not found' >&2\nexit 127\n")
        self._write_exe("gtimeout", "#!/bin/sh\necho 'gtimeout: command not found' >&2\nexit 127\n")
        reconstitute_test_posix = _posix_path(TEST_RECONSTITUTE_FIXES)
        reconst_res = self._run_bash(f'bash "{reconstitute_test_posix}"', env=env, timeout=30)
        self.assertEqual(
            reconst_res.returncode,
            0,
            f"test_reconstitute_fixes.sh failed under BSD-strict sed / no-GNU-timeout:\n{reconst_res.stdout}\n{reconst_res.stderr}",
        )

        for script_path in (HOOK_SCRIPT, TEST_PRE_PUSH_SCRIPT, TEST_RECONSTITUTE_FIXES):
            text = script_path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if "sed -i " in stripped and not stripped.startswith("sed -i.bak"):
                    self.fail(f"Unportable GNU sed -i at {script_path.name}:{lineno}: {stripped}")

    def test_no_bsd_sed_hex_escapes_or_awk_nul_rs(self):
        """json_escape and backup-fleet NUL verdict parsing avoid BSD sed \\xNN and BSD awk RS=\"\\0\"."""
        for script_path in (HOOK_SCRIPT, BACKUP_FLEET_SCRIPT):
            text = script_path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if 'RS="\\0"' in stripped or "RS='\\0'" in stripped:
                    self.fail(f"Unportable BSD awk RS=\\0 at {script_path.name}:{lineno}: {stripped}")
                if stripped.startswith("-e 's/[\\x") or "sed 's/\\x" in stripped or 'sed "s/\\x' in stripped:
                    self.fail(f"Unportable BSD sed \\xNN escape at {script_path.name}:{lineno}: {stripped}")

        hook_posix = _posix_path(HOOK_SCRIPT)
        res = self._run_bash(
            f'source "{hook_posix}"\n'
            'out=$(json_escape "linux_shape_skipped_delete_only_0123456789abcdef")\n'
            'printf "%s" "$out"\n'
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "linux_shape_skipped_delete_only_0123456789abcdef")


if __name__ == "__main__":
    unittest.main()
