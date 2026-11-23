#!/usr/bin/env python3
"""Red tests for driver/headless_cli_driver.py (ledger f-2e9a2cfb, stage S1).

HeadlessCliDriver(AgentDriver) runs a local headless agent CLI as one isolated
worker per dispatch_worker() call. Profiles:
  * "codex-exec"   -> codex exec (prompt on stdin, --cd, --sandbox,
                      --output-last-message). Open-weight: --oss plus
                      --local-provider {ollama,lmstudio}, and -m <model>.
  * "agy-headless" -> agy -p "<prompt>" --output-format json --print-timeout <d>
                      (+ --mode accept-edits, --model <model>).

Contract pinned by these tests (all subprocess calls are STUBBED, no real
codex/agy run, no network, no spend):
  1. argv is a list, no shell; cwd == request.workdir; timeout is bounded.
  2. Explicit env allow-list (never None); cloud API keys never reach env or argv.
  3. text = final message; files_written = changed paths from git in workdir.
  4. ok False + error on nonzero exit, timeout, or missing binary.
  5. Never sets verified (wave_bridge owns it).
  6. Open-weight config puts exactly the provider/model flags in argv.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import WorkerRequest, WorkerResult  # noqa: E402
from headless_cli_driver import HeadlessCliDriver  # noqa: E402

# Real subprocess.run captured before any patching: git calls made by the
# driver (files_written) are delegated to it so they stay real and local.
_REAL_RUN = subprocess.run

# Names of cloud credentials that must never reach a headless CLI child.
_CLOUD_KEY_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY")
_FAKE_SECRET = "test-not-a-real-secret-value"


def _init_repo(path: Path) -> None:
    """Create a real git repo with one committed seed file (local only)."""
    kw = {"cwd": str(path), "check": True, "capture_output": True}
    _REAL_RUN(["git", "init", "-q"], **kw)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _REAL_RUN(["git", "add", "seed.txt"], **kw)
    _REAL_RUN(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test",
         "commit", "-q", "-m", "seed"],
        **kw,
    )


def _make_fake_run(
    returncode=0,
    stdout="",
    stderr="",
    last_message=None,
    write_file=None,
    raise_exc=None,
):
    """Return (fake_run, calls). Non-git argv is recorded, never executed."""
    calls = []

    def fake_run(argv, **kwargs):
        if argv and Path(str(argv[0])).stem.lower() == "git":
            return _REAL_RUN(argv, **kwargs)
        calls.append((list(argv), dict(kwargs)))
        if raise_exc is not None:
            raise raise_exc
        if write_file is not None:
            Path(kwargs["cwd"], write_file).write_text("changed\n", encoding="utf-8")
        if last_message is not None and "--output-last-message" in argv:
            target = argv[argv.index("--output-last-message") + 1]
            Path(target).write_text(last_message, encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    return fake_run, calls


class _RepoCase(unittest.TestCase):
    """Per-test temp git worktree plus a hermetic, secret-laden environment."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name) / "wt"
        self.workdir.mkdir()
        _init_repo(self.workdir)
        leaky = {name: _FAKE_SECRET for name in _CLOUD_KEY_NAMES}
        leaky["AWS_ACCESS_KEY_ID"] = _FAKE_SECRET
        patcher = mock.patch.dict(os.environ, leaky, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, prompt="do the task"):
        return WorkerRequest(
            prompt=prompt,
            owned_files=("new_file.txt",),
            workdir=str(self.workdir),
            label="t1",
        )

    def dispatch(self, profile, fake, **driver_kwargs):
        driver = HeadlessCliDriver(profile, **driver_kwargs)
        with mock.patch("headless_cli_driver.subprocess.run", side_effect=fake):
            return driver.dispatch_worker(self.request())


class TestProfiles(unittest.TestCase):
    def test_codex_exec_profile_name(self):
        self.assertEqual(HeadlessCliDriver("codex-exec").name, "codex-exec")

    def test_agy_headless_profile_name(self):
        self.assertEqual(HeadlessCliDriver("agy-headless").name, "agy-headless")

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            HeadlessCliDriver("peer-thinker")

    def test_agy_rejects_provider_override(self):
        # agy has no provider flag; an open-weight provider must not be silently ignored.
        with self.assertRaises(ValueError):
            HeadlessCliDriver("agy-headless", provider="ollama")

    def test_codex_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            HeadlessCliDriver("codex-exec", provider="openrouter")


class TestCodexExecArgv(_RepoCase):
    def test_argv_is_list_invoking_codex_exec_without_shell(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        argv, kwargs = calls[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(Path(argv[0]).stem.lower(), "codex")
        self.assertEqual(argv[1], "exec")
        self.assertFalse(kwargs.get("shell", False))

    def test_cwd_is_request_workdir(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        self.assertEqual(calls[0][1]["cwd"], str(self.workdir))

    def test_cd_and_sandbox_flags(self):
        # codex exec --help: -C/--cd <DIR>; -s/--sandbox {read-only,workspace-write,danger-full-access}
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        argv = calls[0][0]
        self.assertEqual(argv[argv.index("--cd") + 1], str(self.workdir))
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")

    def test_prompt_is_stdin_not_argv(self):
        # codex exec --help: PROMPT omitted or "-" reads instructions from stdin.
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        argv, kwargs = calls[0]
        self.assertEqual(argv[-1], "-")
        self.assertEqual(kwargs.get("input"), "do the task")
        self.assertFalse(any("do the task" in part for part in argv))

    def test_timeout_is_bounded(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        timeout = calls[0][1].get("timeout")
        self.assertIsInstance(timeout, (int, float))
        self.assertGreater(timeout, 0)

    def test_output_encoding_is_utf8_replace(self):
        # Repo policy: every subprocess call names encoding and errors explicitly.
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        kwargs = calls[0][1]
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")


class TestCodexExecResult(_RepoCase):
    def test_text_is_last_message_file(self):
        fake, _ = _make_fake_run(last_message="FINAL ANSWER 42")
        result = self.dispatch("codex-exec", fake)
        self.assertIsInstance(result, WorkerResult)
        self.assertEqual(result.text, "FINAL ANSWER 42")

    def test_success_is_ok_with_no_error(self):
        fake, _ = _make_fake_run(last_message="done")
        result = self.dispatch("codex-exec", fake)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)

    def test_files_written_reports_git_changes(self):
        fake, _ = _make_fake_run(last_message="done", write_file="new_file.txt")
        result = self.dispatch("codex-exec", fake)
        self.assertIn("new_file.txt", result.files_written)

    def test_nonzero_exit_is_not_ok(self):
        fake, _ = _make_fake_run(returncode=2, stderr="boom: bad flag", last_message=None)
        result = self.dispatch("codex-exec", fake)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertIn("2", result.error)

    def test_timeout_is_not_ok(self):
        exc = subprocess.TimeoutExpired(cmd="codex", timeout=5)
        fake, _ = _make_fake_run(raise_exc=exc)
        result = self.dispatch("codex-exec", fake)
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error.lower())

    def test_missing_binary_is_not_ok(self):
        exc = FileNotFoundError(2, "No such file or directory", "codex")
        fake, _ = _make_fake_run(raise_exc=exc)
        result = self.dispatch("codex-exec", fake)
        self.assertFalse(result.ok)
        self.assertIn("codex", result.error.lower())

    def test_never_sets_verified(self):
        fake, _ = _make_fake_run(last_message="done", write_file="new_file.txt")
        result = self.dispatch("codex-exec", fake)
        self.assertIsNone(result.verified)


class TestEnvAllowList(_RepoCase):
    def test_env_is_explicit_and_drops_cloud_keys(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        env = calls[0][1].get("env")
        self.assertIsNotNone(env, "env=None would inherit every secret in os.environ")
        for name in _CLOUD_KEY_NAMES:
            self.assertNotIn(name, env)
        self.assertNotIn("AWS_ACCESS_KEY_ID", env)

    def test_secret_values_never_appear_in_argv(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        argv = calls[0][0]
        self.assertFalse(any(_FAKE_SECRET in part for part in argv))


class TestOpenWeightCodex(_RepoCase):
    def test_ollama_profile_puts_oss_provider_and_model_in_argv(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake, provider="ollama", model="qwen2.5-coder:32b")
        argv = calls[0][0]
        # codex exec --help: --oss "Use open-source provider";
        # --local-provider <OSS_PROVIDER> (lmstudio or ollama); -m/--model <MODEL>.
        self.assertIn("--oss", argv)
        self.assertEqual(argv[argv.index("--local-provider") + 1], "ollama")
        self.assertEqual(argv[argv.index("-m") + 1], "qwen2.5-coder:32b")

    def test_lmstudio_profile_uses_lmstudio_provider(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake, provider="lmstudio", model="qwen2.5-coder-7b")
        argv = calls[0][0]
        self.assertIn("--oss", argv)
        self.assertEqual(argv[argv.index("--local-provider") + 1], "lmstudio")

    def test_open_weight_profile_passes_no_cloud_key_env(self):
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake, provider="ollama", model="qwen2.5-coder:32b")
        env = calls[0][1].get("env")
        self.assertIsNotNone(env)
        for name in _CLOUD_KEY_NAMES:
            self.assertNotIn(name, env)

    def test_no_model_or_provider_configured_adds_no_model_flags(self):
        # No hardcoded model: unset config means no -m and no --oss in argv.
        fake, calls = _make_fake_run(last_message="ok")
        self.dispatch("codex-exec", fake)
        argv = calls[0][0]
        self.assertNotIn("-m", argv)
        self.assertNotIn("--oss", argv)


class TestAgyHeadless(_RepoCase):
    def test_argv_is_agy_print_json_with_prompt_as_one_element(self):
        # agy --help: -p/--print/--prompt; --output-format (text, json, stream-json);
        # --print-timeout (0 waits until the turn completes).
        fake, calls = _make_fake_run(stdout='{"result": "AGY_MARKER"}')
        self.dispatch("agy-headless", fake)
        argv, kwargs = calls[0]
        self.assertEqual(Path(argv[0]).stem.lower(), "agy")
        self.assertNotIn("shell", kwargs)
        self.assertEqual(argv[argv.index("-p") + 1], "do the task")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertIn("--print-timeout", argv)
        self.assertTrue(argv[argv.index("--print-timeout") + 1])
        self.assertEqual(kwargs["cwd"], str(self.workdir))

    def test_mode_accept_edits_so_headless_edits_do_not_block(self):
        # agy --help: --mode {accept-edits, auto, plan}.
        fake, calls = _make_fake_run(stdout="{}")
        self.dispatch("agy-headless", fake)
        argv = calls[0][0]
        self.assertEqual(argv[argv.index("--mode") + 1], "accept-edits")

    def test_model_override_passes_model_flag(self):
        fake, calls = _make_fake_run(stdout="{}")
        self.dispatch("agy-headless", fake, model="gemini-flash-test")
        argv = calls[0][0]
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-flash-test")

    def test_no_model_configured_adds_no_model_flag(self):
        fake, calls = _make_fake_run(stdout="{}")
        self.dispatch("agy-headless", fake)
        self.assertNotIn("--model", calls[0][0])

    def test_env_is_explicit_and_drops_cloud_keys(self):
        fake, calls = _make_fake_run(stdout="{}")
        self.dispatch("agy-headless", fake)
        env = calls[0][1].get("env")
        self.assertIsNotNone(env)
        for name in _CLOUD_KEY_NAMES:
            self.assertNotIn(name, env)

    def test_text_carries_agy_stdout_payload(self):
        fake, _ = _make_fake_run(stdout='{"result": "AGY_MARKER"}')
        result = self.dispatch("agy-headless", fake)
        self.assertTrue(result.ok)
        self.assertIn("AGY_MARKER", result.text)

    def test_files_written_reports_git_changes(self):
        fake, _ = _make_fake_run(stdout="{}", write_file="new_file.txt")
        result = self.dispatch("agy-headless", fake)
        self.assertIn("new_file.txt", result.files_written)

    def test_nonzero_exit_is_not_ok(self):
        fake, _ = _make_fake_run(returncode=1, stderr="agy failed")
        result = self.dispatch("agy-headless", fake)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_timeout_is_not_ok(self):
        exc = subprocess.TimeoutExpired(cmd="agy", timeout=5)
        fake, _ = _make_fake_run(raise_exc=exc)
        result = self.dispatch("agy-headless", fake)
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error.lower())

    def test_missing_binary_is_not_ok(self):
        exc = FileNotFoundError(2, "No such file or directory", "agy")
        fake, _ = _make_fake_run(raise_exc=exc)
        result = self.dispatch("agy-headless", fake)
        self.assertFalse(result.ok)
        self.assertIn("agy", result.error.lower())

    def test_never_sets_verified(self):
        fake, _ = _make_fake_run(stdout="{}", write_file="new_file.txt")
        result = self.dispatch("agy-headless", fake)
        self.assertIsNone(result.verified)


if __name__ == "__main__":
    unittest.main()
