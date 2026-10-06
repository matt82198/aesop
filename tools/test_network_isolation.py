#!/usr/bin/env python3
"""
Harness-level network/remote isolation for the Python test suite.
INDEX: Makes the Python test PROCESS structurally incapable of reaching the real git
remote or GitHub API (incident 2026-10-05: an unmocked subprocess test pushed six
`integrate/batch-*` branches to real origin). Rewrites every github.com URL to a
throwaway local bare sink via GIT_CONFIG_COUNT/KEY_n/VALUE_n `insteadOf`, sets
GIT_TERMINAL_PROMPT=0, blocks `gh` via GH_TOKEN=""/GH_HOST=localhost.invalid/
GH_CONFIG_DIR=<empty tmp dir>, and sets AESOP_TEST_NO_NETWORK=1. `apply_test_isolation_env()`
is the per-process entry point, called once from tests/__init__.py (the one place
guaranteed to execute before any `tests.test_*` module, regardless of whether the
suite is run via pytest, `unittest discover`, or tools/ci_shard_runner.py) and
defensively again from ci_shard_runner.py's own entry point; idempotent per process.
stdlib-only.

Why this exists (see tests/test_network_isolation.py for the behavioral proof, and
tests/CLAUDE.md / LANE-CONTRACT.md for the one-line pointer): per-test mocking of
`gh`/`git` is not a gate -- a test author can always forget it, exactly as happened
with tests/test_merge_train_halt_enforcement.py and
tests/test_merge_queue_halt_enforcement.py (PR #777), which ran tools/merge_train.py
and tools/merge_queue.py as REAL subprocesses with `env = os.environ.copy()` once halt
was cleared. This module does not change what a test author has to remember; it
removes the real remote from the process environment entirely, for the whole run.

Node tests do not need this: they already construct their own local bare-repo
remotes (see tests/test_orchestration_core.test.mjs's `initBareRemote`) and never
shell out to `gh`; tests/helpers/isolated-env.mjs (a separate, HOME-isolation
concern) covers that harness.

Windows note on the `gh` CLI specifically: Win32 CreateProcess only auto-resolves a
bare command name (e.g. `["gh", ...]`, as every caller in this repo invokes it) by
appending `.exe` -- never `.bat`/`.cmd` -- so a PATH-shim SCRIPT in front of the real
`gh.exe` is not reliably interceptable without shipping a compiled binary into the
repo, which this module deliberately does not do. The GH_HOST / GH_CONFIG_DIR /
GH_TOKEN environment overrides below are therefore the real, cross-platform
mechanism that neutralizes `gh` on every OS (they make the real `gh.exe` itself
unable to authenticate or resolve a real host); verified behaviorally in
tests/test_network_isolation.py against a real `gh` binary when one is installed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_APPLIED_FLAG = "AESOP_TEST_ORIGIN_SINK"

# Every literal prefix that must never reach a real GitHub remote.
REWRITTEN_PREFIXES = (
    "https://github.com/",
    "git@github.com:",
)

_PROBE_TIMEOUT = 15


def resolve_remote_suffix(repo_root) -> str:
    """Read (never write, never touch the network) the real `origin` remote URL
    configured for `repo_root`, and return the `<owner>/<name>.git` suffix after
    stripping a recognized github.com prefix. Falls back to a generic placeholder
    if origin is unconfigured, unreadable, or not a github.com URL."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown/unknown.git"
    if result.returncode != 0:
        return "unknown/unknown.git"
    url = result.stdout.strip()
    for prefix in REWRITTEN_PREFIXES:
        if url.startswith(prefix):
            suffix = url[len(prefix):]
            break
    else:
        return "unknown/unknown.git"
    if not suffix.endswith(".git"):
        suffix += ".git"
    return suffix


def make_bare_sink(sink_root, suffix: str) -> Path:
    """Create (idempotently) a throwaway local bare repo at `sink_root/<suffix>`,
    e.g. `sink_root/matt82198/aesop.git`. Nesting it at the SAME relative path a
    real github.com URL would have after its prefix is stripped is what makes the
    `insteadOf` prefix-rewrite below land on a valid, existing repo instead of a
    garbled path (`insteadOf` only replaces the matched prefix and appends the
    rest verbatim -- it does not path-join)."""
    sink_root = Path(sink_root)
    sink_path = sink_root / suffix
    sink_path.parent.mkdir(parents=True, exist_ok=True)
    if not (sink_path / "HEAD").exists():
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(sink_path)],
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_PROBE_TIMEOUT,
        )
    return sink_path


def _as_dir_uri(path: Path) -> str:
    """file:// URI for `path`, guaranteed to end with '/' so insteadOf's verbatim
    prefix-replace-and-append produces a clean nested path on every OS."""
    uri = path.resolve().as_uri()
    return uri if uri.endswith("/") else uri + "/"


def build_git_config_override(sink_root, start_index: int = 0):
    """Return (count, {GIT_CONFIG_KEY_i/GIT_CONFIG_VALUE_i: ...}) entries that
    rewrite every recognized github.com URL prefix to land under `sink_root`."""
    base_uri = _as_dir_uri(Path(sink_root))
    entries = {}
    i = start_index
    for prefix in REWRITTEN_PREFIXES:
        entries[f"GIT_CONFIG_KEY_{i}"] = f"url.{base_uri}.insteadOf"
        entries[f"GIT_CONFIG_VALUE_{i}"] = prefix
        i += 1
    return i - start_index, entries


def build_isolation_env(sink_root, gh_config_dir=None) -> dict:
    """Pure: build the full set of environment variables that make the CURRENT
    process (and any subprocess that inherits this env) structurally incapable of
    reaching the real git remote or GitHub API. Never touches os.environ or the
    real filesystem outside of (optionally) `gh_config_dir`."""
    sink_root = Path(sink_root)
    count, git_config_entries = build_git_config_override(sink_root)

    if gh_config_dir is None:
        gh_config_dir = sink_root / "gh-config-empty"
    gh_config_dir = Path(gh_config_dir)
    gh_config_dir.mkdir(parents=True, exist_ok=True)

    env = dict(git_config_entries)
    env["GIT_CONFIG_COUNT"] = str(count)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_TOKEN"] = ""
    env["GH_HOST"] = "localhost.invalid"
    env["GH_CONFIG_DIR"] = str(gh_config_dir)
    env["AESOP_TEST_NO_NETWORK"] = "1"
    return env


def apply_test_isolation_env(force: bool = False) -> dict:
    """Impure, idempotent-per-process entry point: mutates os.environ so the
    CURRENT process -- and every subprocess it spawns that inherits the
    environment (the default for `subprocess.run(..., env=os.environ.copy())` or
    no `env=` at all) -- cannot reach the real origin or GitHub. Returns the dict
    of variables set, or {} if isolation was already active in this process and
    `force` was not passed.

    Deliberately process-scoped, not a context manager: the whole point is that
    a test author doing nothing special still inherits this, for the entire test
    session.
    """
    if not force and os.environ.get(_APPLIED_FLAG):
        return {}

    repo_root = Path(__file__).resolve().parent.parent
    base = Path(tempfile.mkdtemp(prefix="aesop-test-isolation-"))
    suffix = resolve_remote_suffix(repo_root)
    sink_root = base / "sink-root"
    sink_path = make_bare_sink(sink_root, suffix)

    updates = build_isolation_env(sink_root, gh_config_dir=base / "gh-config-empty")
    updates[_APPLIED_FLAG] = str(sink_path)

    os.environ.update(updates)
    return updates


def origin_sink_path():
    """Path of the bare sink repo active in THIS process, or None if isolation has
    not been applied yet."""
    value = os.environ.get(_APPLIED_FLAG)
    return Path(value) if value else None


if __name__ == "__main__":
    # Smoke-test CLI: apply isolation and print what was set, for manual checks.
    applied = apply_test_isolation_env(force=True)
    for key in sorted(applied):
        print(f"{key}={applied[key]}")
    sys.exit(0)
