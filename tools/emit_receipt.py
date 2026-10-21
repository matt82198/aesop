#!/usr/bin/env python3
"""Run the local verification matrix and publish a signed receipt bound to HEAD.
INDEX: Receipt-gate increment 1 -- runs the configured local matrix (default: the 4 python shards via `ci_shard_runner.py n 4` plus the pre-push gate set; `--matrix a,b,c` to subset; parts that cannot run on this box, e.g. `browser-proofs`, are recorded as skipped with a reason, never silently dropped), collects `{schema:1, schema_version:1, repo, head_sha, base_sha (merge-base with trunk_ref, the trunk resolved like the pre-push hook: origin/HEAD, origin/main, origin/master, else HEAD on a fresh remote), tree_hash (= git rev-parse HEAD^{tree}), parts:[{name, exit_code, test_count, duration_s}], skipped, host:{os, python, hostname_hash}, timestamp}`, canonicalizes (sorted keys, no whitespace) and signs it (`--scheme auto|ed25519|hmac-sha256`; Ed25519 via `cryptography` when importable with the private key at `$AESOP_RECEIPT_KEY`, else HMAC-SHA256 keyed by `$AESOP_RECEIPT_HMAC_SECRET`; the key NEVER lives in the repo), then `--post` publishes it on the head sha as a GitHub check-run named `verify-receipt-local` (conclusion from the parts, receipt JSON in output.text) and, when the token cannot create check-runs (HTTP 403: only GitHub Apps may), falls back to a commit comment carrying the same JSON under a marker; `--dry-run` prints/writes the envelope and performs NO network call. The receipt is NOT a committed file (self-reference + merge conflicts). Exit 0 = receipt produced (parts may still be red: that is what the verifier judges), 2 = could not produce/publish (no key material, unknown part, both publish channels failed). stdlib-only; `cryptography` optional.

Trust boundary (see docs/RECEIPT-GATE.md): a signed receipt proves only that the key
holder ran these parts on a tree with this hash and got these exit codes. The hosted
verifier (tools/verify_receipt.py via .github/workflows/verify-receipt.yml) recomputes
the tree hash from ITS checkout and never trusts the number in the receipt. Honesty
about whether the parts were really run comes from increment 6 (random hosted re-run +
main-full cross-check), not from this tool.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc  # noqa: E402  (sys.path adjusted above)

ReceiptError = rc.ReceiptError

_TEST_COUNT_RES = (
    re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE),          # unittest (ci_shard_runner default)
    re.compile(r"\b(\d+) passed\b"),                            # pytest summary
)


class PartSpec:
    """One matrix part: `cmd` builds argv from the repo path; `cmd is None` means not runnable here."""

    def __init__(self, cmd, skip_reason=None, requires_wsl=False):
        self.cmd = cmd
        self.skip_reason = skip_reason
        # requires_wsl: the Linux-shape leg. WSL probe absent/no-distro -> skipped-not-runnable;
        # probe error -> the part runs and fails closed (see run_matrix).
        self.requires_wsl = requires_wsl


def _py(*rest):
    return lambda repo: [sys.executable] + [str(repo / r) if r.startswith("tools/") else r for r in rest]


DEFAULT_REGISTRY = {
    # Same argv as ci.yml (`--timing-file`): the runner's bucket assignment is then the one
    # _build_shard_map derives. The path is relative to the part's cwd (the throwaway worktree).
    "py-shard-0": _py("tools/ci_shard_runner.py", "0", "4", "--timing-file", ".github/shard-timing.json"),
    "py-shard-1": _py("tools/ci_shard_runner.py", "1", "4", "--timing-file", ".github/shard-timing.json"),
    "py-shard-2": _py("tools/ci_shard_runner.py", "2", "4", "--timing-file", ".github/shard-timing.json"),
    "py-shard-3": _py("tools/ci_shard_runner.py", "3", "4", "--timing-file", ".github/shard-timing.json"),
    "secret-scan": _py("tools/secret_scan.py", "."),
    "claudemd-sync-gate": _py("tools/claudemd_sync_gate.py", "--check"),
    "gen-tool-index": _py("tools/gen_tool_index.py", "--check"),
    "verify-test-suite-count": _py("tools/verify_test_suite_count.py", "--check"),
    "encoding-lint": _py("tools/encoding_lint.py", "--check"),
    "import-resolution-check": _py("tools/import_resolution_check.py"),
    "sibling-import-check": _py("tools/sibling_import_check.py", "--check"),
    "linux-shape": PartSpec(_py("tools/linux_shape_check.py", "--require-wsl"), requires_wsl=True),
    "browser-proofs": PartSpec(None, "not runnable here: hosted browsers + playwright matrix (ci.yml browser-proofs)"),
    "windows-shard": PartSpec(None, "not runnable here: hosted windows runner (ci.yml windows-shard)"),
}
for _name, _spec in list(DEFAULT_REGISTRY.items()):
    if not isinstance(_spec, PartSpec):
        DEFAULT_REGISTRY[_name] = PartSpec(_spec)
DEFAULT_MATRIX = [n for n in DEFAULT_REGISTRY]
_FAST_PARTS = {"secret-scan", "claudemd-sync-gate", "gen-tool-index",
               "verify-test-suite-count", "encoding-lint", "import-resolution-check",
               "sibling-import-check"}
_PY_SHARDS = {"py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3"}


def _resolve_profile(environ):
    """Resolve receipt profile from AESOP_RECEIPT_PROFILE env var (default: scoped)."""
    profile = environ.get("AESOP_RECEIPT_PROFILE", "scoped").strip().lower()
    return "scoped" if profile not in ("scoped", "full") else profile


def _get_test_files(repo):
    """Get list of test files from git ls-files. Returns None if cannot determine.
    The `:(top)` magic anchors the pathspec at the worktree root, so a cwd inside a subdirectory
    still sees every tracked test file (plain `tests/...` is cwd-relative in git)."""
    try:
        output = _git(repo, "ls-files", ":(top)tests/test_*.py")
        if not output:
            return []
        return sorted([Path(line).stem for line in output.split("\n") if line])
    except ReceiptError:
        return None


def _build_shard_map(shard_names, test_files=None, timing=None, repo=None):
    """Build a map of test file stems to owning shard names. Returns None if unable.

    Uses the SAME assignment as ci_shard_runner.py: greedy timing-aware bin-packing when a
    non-empty timing map is given (CI passes .github/shard-timing.json), otherwise round-robin.
    `test_files` / `timing` are injectable for tests; the caller passes the CI timing map so
    the emitter cannot drift from the runner's assignment. Discovery is anchored at `repo`
    (default: this checkout's root), never at the process cwd."""
    if test_files is None:
        test_files = _get_test_files(_REPO_ROOT if repo is None else repo)
    if test_files is None:
        return None

    import ci_shard_runner  # tools/ is on sys.path; the single source of the shard assignment
    total = len(shard_names)
    if timing:
        buckets = ci_shard_runner.distribute_shards_by_timing(test_files, total, timing)
    else:
        buckets = [ci_shard_runner.distribute_shards(test_files, i, total) for i in range(total)]

    shard_map = {}
    for shard_idx, bucket in enumerate(buckets):
        for stem in bucket:
            shard_map[stem] = shard_names[shard_idx]
    return shard_map


def _load_ci_timing(repo):
    """The timing map exactly as ci.yml passes it (`--timing-file .github/shard-timing.json`)."""
    import ci_shard_runner
    return ci_shard_runner.load_timing_data(str(Path(repo) / ".github" / "shard-timing.json"))


def _find_owning_shards(changed_files, shard_map):
    """Map changed files to owning shards. Returns a set of shard names, or None (underivable:
    the caller runs the FULL set) when the shard map is missing or a tools/ change has no owner.
    A tools/ change that no shard owns (e.g. tools/receipt_common.py) must never narrow to zero."""
    if shard_map is None:
        return None  # Underivable

    owning = set()
    for file_path in changed_files:
        path = Path(file_path)
        # Only map test and tools Python files to shards
        if path.suffix == ".py" and path.parent.name in ("tests", "tools"):
            stem = path.stem
            if stem in shard_map:
                owning.add(shard_map[stem])
                continue
        if path.parts[:1] == ("tools",):
            return None  # fail closed: unowned tools/ change selects the full set
    return owning


def _build_parts_and_skipped_for_profile(profile, changed_files, owning_shards, registry):
    """Build parts and skipped lists for the given profile.
    Returns (parts_list, skipped_list) of dicts with 'name' keys."""
    parts = []
    skipped = []

    if profile == "full":
        # Full profile: include all shards + fast parts
        for name in sorted(registry.keys()):
            if name in _FAST_PARTS or name in _PY_SHARDS:
                parts.append({"name": name, "runnable": registry[name].cmd is not None})
            elif registry[name].cmd is None:
                skipped.append({"name": name, "reason": registry[name].skip_reason or "not runnable here"})
            else:
                parts.append({"name": name, "runnable": True})
    else:  # scoped
        # Scoped profile: fast parts + only owned shards
        for name in sorted(registry.keys()):
            if name in _FAST_PARTS:
                parts.append({"name": name, "runnable": registry[name].cmd is not None})
            elif name in _PY_SHARDS:
                if owning_shards is not None and name in owning_shards:
                    parts.append({"name": name, "runnable": registry[name].cmd is not None})
                else:
                    reason = "not in scope: no changed file maps to this shard (profile=scoped)"
                    skipped.append({"name": name, "reason": reason})
            elif registry[name].cmd is None:
                skipped.append({"name": name, "reason": registry[name].skip_reason or "not runnable here"})
            else:
                parts.append({"name": name, "runnable": True})

    # Filter to only names from registry
    parts_out = []
    for part in parts:
        if part["name"] in registry and registry[part["name"]].cmd is not None:
            parts_out.append(part["name"])

    return parts_out, skipped


def _build_parts_for_profile(profile, changed_files, owning_shards, registry):
    """Build list of runnable part names for the profile. Returns list of part names."""
    parts, _ = _build_parts_and_skipped_for_profile(profile, changed_files, owning_shards, registry)
    return parts


def _git(repo, *args):
    res = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                         encoding="utf-8", errors="replace", timeout=60)
    if res.returncode != 0:
        raise ReceiptError("git %s failed: %s" % (" ".join(args), (res.stderr or "").strip()))
    return res.stdout.strip()


def parse_test_count(text):
    for rx in _TEST_COUNT_RES:
        m = rx.search(text or "")
        if m:
            return int(m.group(1))
    return None


# Variables that are inputs to the pre-push HOOK (push-time overrides and lane-control state),
# never to the test parts. A part that inherits one can see a push-time escape hatch or a lane's
# state root and fail tests that assert those are unset (incident 2026-10-15, PR #1008: a lane's
# AESOP_ALLOW_GENERATED=1 turned two TestBatchRegeneration tests red on a false receipt).
# Defense in depth also covers the GIT_* hook variables: inherited GIT_DIR/GIT_WORK_TREE let a
# matrix part commit into the caller's repo. See RECEIPT-GATE.md and hooks/CLAUDE.md.
SCRUBBED_ENV_VARS = (
    "AESOP_ALLOW_GENERATED", "AESOP_PUSH_RED", "AESOP_RECEIPT_EMIT", "AESOP_REQUIRE_LINUX_SHAPE",
    "AESOP_STATE_ROOT", "AESOP_ROOT", "AESOP_CONDUCTOR3_ROOT", "CONDUCTOR_ROOT",
    # Receipt SIGNING material: a part runs the pushed head's test code, so an inherited HMAC
    # secret (or key path) would let that code forge an accepted receipt. Only the parent
    # signing call in main() reads these; matrix parts never do.
    "AESOP_RECEIPT_KEY", "AESOP_RECEIPT_HMAC_SECRET",
    # ci_shard_runner._arm_isolation_tripwire() returns without running the isolation tripwire when
    # this is set, so an inherited value would run the receipt matrix with the tripwire disabled.
    "AESOP_CI_SHARD_TRIPWIRE_ARMED",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
)


def scrub_child_env(environ):
    """Return (child_env, removed_names): a copy of `environ` with SCRUBBED_ENV_VARS removed.
    Only NAMES are returned, for the receipt; values never leave this function."""
    env = dict(environ)
    removed = []
    for var in SCRUBBED_ENV_VARS:
        if var in env:
            del env[var]
            removed.append(var)
    return env, sorted(removed)


# Restored from the PR branch's commit 946a75e5 (escape-hatch scrub). Main's #1021 superseded
# the list and helper with SCRUBBED_ENV_VARS / scrub_child_env; these names are aliases over
# that single source of truth so the escape-hatch behaviour is asserted in one place.
_SCRUBBED_PART_ENV = SCRUBBED_ENV_VARS


def scrubbed_part_env(environ):
    """Return a copy of environ with the push-inherited variables removed (see scrub_child_env)."""
    return scrub_child_env(environ)[0]


PART_TIMEOUT_ENV = "AESOP_RECEIPT_PART_TIMEOUT"
# Below the pre-push hook's outer AESOP_RECEIPT_TIMEOUT (900s): a hung part goes RED on its own,
# instead of the outer ceiling killing the hook and stranding the push with children orphaned.
PART_TIMEOUT_DEFAULT_S = 600.0
PART_TIMEOUT_EXIT = 124  # the coreutils `timeout` convention; any non-zero exit is red


def part_timeout_seconds(environ=None):
    """Per-part wall-clock bound in seconds. Invalid, zero, negative or non-finite values fall back
    to the default and say so on stderr: a bad value never disables the bound."""
    environ = os.environ if environ is None else environ
    raw = environ.get(PART_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return PART_TIMEOUT_DEFAULT_S
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        print("emit_receipt: invalid %s=%r; using the default %ds (the bound is never disabled)"
              % (PART_TIMEOUT_ENV, raw, int(PART_TIMEOUT_DEFAULT_S)), file=sys.stderr)
        return PART_TIMEOUT_DEFAULT_S
    return value


def _kill_process_tree(proc):
    """Kill proc and every descendant. Windows: taskkill /T; POSIX: the whole session group."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30)
        except subprocess.TimeoutExpired:
            pass  # fall through to the direct kill below
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # start_new_session=True made proc the group leader
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()


def run_part(repo, name, spec, throwaway_wt=None, log_path=None, environ=None):
    argv = spec.cmd(Path(throwaway_wt or repo))
    t0 = time.monotonic()
    source = os.environ if environ is None else environ
    env, _removed = scrub_child_env(source)
    budget = part_timeout_seconds(source)

    with tempfile.TemporaryDirectory(prefix="receipt-%s-" % name) as tmp:
        env["TMPDIR"] = tmp  # per-part temp dir, mirrors ci.yml's shard-specific TMPDIR
        proc = subprocess.Popen(argv, cwd=str(throwaway_wt or repo), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                                start_new_session=(os.name != "nt"))
        try:
            out_text, err_text = proc.communicate(timeout=budget)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                out_text, err_text = proc.communicate(timeout=30)  # drain; pipes close once the tree is gone
            except subprocess.TimeoutExpired:
                out_text, err_text = "", ""
            returncode = PART_TIMEOUT_EXIT
            err_text = (err_text or "") + ("\nemit_receipt: part %s timed out after %ss; killed its process tree (FAILED)\n"
                                           % (name, "%g" % budget))
    res = subprocess.CompletedProcess(argv, returncode, out_text, err_text)
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    if res.returncode != 0 and log_path is not None:
        # A failing part must be explainable: keep its output tail on disk (never in the envelope).
        _write_part_log(Path(log_path), res)
    return {"name": name, "exit_code": int(res.returncode), "test_count": parse_test_count(out),
            "duration_s": round(time.monotonic() - t0, 3)}


PART_LOG_TAIL_LINES = 200


def part_log_path(repo, head_sha, name):
    """Where a FAILED part's output tail is kept: state/receipts/parts/<sha>/<part>.log (gitignored state/)."""
    return Path(repo) / "state" / "receipts" / "parts" / head_sha / (name + ".log")


def _write_part_log(log_path, res):
    """Keep the last PART_LOG_TAIL_LINES lines of a failed part's stdout and stderr."""
    def tail(text):
        return "\n".join((text or "").splitlines()[-PART_LOG_TAIL_LINES:])
    body = "--- stdout (last %d lines) ---\n%s\n--- stderr (last %d lines) ---\n%s\n" % (
        PART_LOG_TAIL_LINES, tail(res.stdout), PART_LOG_TAIL_LINES, tail(res.stderr))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(body, encoding="utf-8")


PART_LOG_DIR_REL = "state/receipts/parts"  # the emitter's own writes; never a matrix mutation


def _capture_caller_state(repo):
    """Capture the caller repo's git state for tripwire verification.
    Returns None if repo is not a git repository (e.g., test temp directory).
    The emitter's own part-log directory is excluded: writing it is the emitter's
    job, and state/ is not guaranteed git-ignored in adopter repos."""
    repo = Path(repo)
    try:
        return {
            "head": _git(repo, "rev-parse", "HEAD"),
            "tree": _git(repo, "write-tree"),
            "porcelain": subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain", "--", ".", ":(exclude)" + PART_LOG_DIR_REL],
                capture_output=True, encoding="utf-8", errors="replace").stdout,
        }
    except ReceiptError:
        return None  # Not a git repo; skip tripwire verification


def _verify_caller_untouched(repo, state_before):
    """Tripwire: verify the caller repo was not mutated. Exit non-zero if mutated.
    Skips verification if repo is not a git repository."""
    if state_before is None:
        return  # Caller is not a git repo; nothing to verify

    state_after = _capture_caller_state(repo)
    if state_after is None:
        raise ReceiptError("ERROR: matrix removed the caller repo's git metadata")

    if state_before["head"] != state_after["head"]:
        raise ReceiptError("ERROR: matrix mutated the caller tree (HEAD changed)")
    if state_before["tree"] != state_after["tree"]:
        raise ReceiptError("ERROR: matrix mutated the caller tree (index changed)")
    if state_before["porcelain"] != state_after["porcelain"]:
        raise ReceiptError("ERROR: matrix mutated the caller tree (working tree changed)")


def _remove_throwaway_worktree(repo, wt):
    """Remove a detached worktree and clean up."""
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                      capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"],
                      capture_output=True, timeout=30)
    except Exception:
        pass  # Best effort cleanup


def _default_wsl_status():
    import linux_shape_check  # tools/ is on sys.path (see module header)
    return linux_shape_check.wsl_status()


def run_matrix(repo, names, registry=None, jobs=1, wsl_status_fn=None):
    """Run every runnable part (in parallel up to `jobs`) in a detached throwaway worktree;
    return (parts, skipped). Caller tree is verified untouched via tripwire.

    A part with requires_wsl is skipped (not failed) when the WSL probe reports `absent` or
    `no-distro`. On `error` it still runs: its command (linux_shape_check --require-wsl) fails
    closed, so a broken probe can never turn into a silent skip."""
    registry = DEFAULT_REGISTRY if registry is None else registry
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ReceiptError("unknown matrix part(s): %s (known: %s)" % (", ".join(unknown), ", ".join(sorted(registry))))
    skipped, runnable = [], []
    wsl_status = None
    for n in names:
        spec = registry[n]
        if spec.cmd is None:
            skipped.append({"name": n, "reason": spec.skip_reason or "not runnable here"})
        elif getattr(spec, "requires_wsl", False):
            if wsl_status is None:
                wsl_status = (wsl_status_fn or _default_wsl_status)()
            if wsl_status in ("absent", "no-distro"):
                skipped.append({"name": n, "reason": "not runnable here: WSL distro unavailable (%s); "
                                                     "hosted Linux CI runs this leg" % wsl_status})
            else:
                runnable.append(n)
        else:
            runnable.append(n)

    # Capture caller state before matrix runs
    state_before = _capture_caller_state(repo)

    # Create a detached throwaway worktree for the matrix (only if repo is a git repo)
    wt = None
    tmp_root = None
    if state_before is not None:
        # repo is a git repository; create worktree
        tmp_root = Path(tempfile.mkdtemp(prefix="aesop-receipt-matrix-"))
        wt = tmp_root / "wt"
        head_sha = _git(repo, "rev-parse", "HEAD")

        # Create detached worktree at HEAD
        res = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(wt), head_sha],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        if res.returncode != 0:
            raise ReceiptError("failed to create throwaway worktree: %s" % (res.stderr or "").strip())

    try:
        # Run all parts in the throwaway worktree (or caller repo if not a git repo)
        parts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(jobs))) as pool:
            log_dir_sha = state_before["head"] if state_before is not None else None
            futs = {pool.submit(run_part, repo, n, registry[n], wt,
                                part_log_path(repo, log_dir_sha, n) if log_dir_sha else None): n
                    for n in runnable}
            for fut in concurrent.futures.as_completed(futs):
                parts.append(fut.result())
        parts.sort(key=lambda p: names.index(p["name"]))
    finally:
        # Cleanup: remove the throwaway worktree
        if wt is not None:
            _remove_throwaway_worktree(repo, wt)
            try:
                shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass  # Best effort

    # Tripwire: verify caller tree is untouched
    _verify_caller_untouched(repo, state_before)

    return parts, skipped


def repo_slug(repo):
    try:
        url = _git(repo, "remote", "get-url", "origin")
    except ReceiptError:
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


TRUNK_FALLBACKS = ("origin/main", "origin/master")
BOOTSTRAP_TRUNK = "HEAD"


def _ref_resolves(repo, ref):
    try:
        _git(repo, "rev-parse", "--verify", "-q", ref + "^{commit}")
        return True
    except ReceiptError:
        return False


def resolve_trunk(repo, requested=None):
    """Name of the ref a receipt's base_sha is measured against.

    Mirrors hooks/pre-push-policy.sh gate_trunk_ref: an explicit ref that resolves wins;
    otherwise origin/HEAD's symbolic target, then origin/main, then origin/master. When
    origin has no trunk yet (a fresh remote: BOOTSTRAP) the base is HEAD, so base_sha == head_sha.
    """
    if requested and _ref_resolves(repo, requested):
        return requested
    try:
        head_ref = _git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    except ReceiptError:
        head_ref = ""
    prefix = "refs/remotes/origin/"
    if head_ref.startswith(prefix):
        name = "origin/" + head_ref[len(prefix):]
        if _ref_resolves(repo, name):
            return name
    for name in TRUNK_FALLBACKS:
        if _ref_resolves(repo, name):
            return name
    return BOOTSTRAP_TRUNK


def non_github_remote(repo):
    """Why the check-run POST cannot happen, or None when it should be attempted.

    A check-run only exists on github.com. Only an origin that is configured AND not a
    github.com remote is skipped (local path, file:// or another host). No origin at all
    returns None, so the POST is attempted and fails closed as before. A GitHub remote
    always returns None: a GitHub error on it still blocks.
    """
    try:
        # The raw configured URL: `remote get-url` applies insteadOf rewrites, which the test
        # harness uses to point github.com at a local sink and would hide the real host.
        url = _git(repo, "config", "--get", "remote.origin.url")
    except ReceiptError:
        return None
    if "://" in url:
        parsed = urlparse(url)
        if parsed.scheme == "file" or not parsed.hostname:
            remote = "local-path"
        elif parsed.hostname.lower() == "github.com":
            return None
        else:
            remote = "other-host"
    else:
        scp = re.match(r"^[^@/\\]+@([^:/\\]+):", url)
        if scp is None:
            remote = "local-path"  # a bare path such as C:/... or /srv/remote.git
        elif scp.group(1).lower() == "github.com":
            return None
        else:
            remote = "other-host"
    return {"skipped": "non-github-remote", "remote": remote}


def build_receipt(repo, parts, skipped, slug=None, main_ref=None, profile="scoped"):
    repo = Path(repo)
    head = _git(repo, "rev-parse", "HEAD")
    trunk = resolve_trunk(repo, main_ref)
    return {
        "schema": 1,
        "schema_version": 1,
        "profile": profile,
        "repo": slug or repo_slug(repo) or "unknown",
        "head_sha": head,
        "base_sha": _git(repo, "merge-base", head, trunk),
        "trunk_ref": trunk,
        "tree_hash": _git(repo, "rev-parse", head + "^{tree}"),
        "parts": parts,
        "skipped": skipped,
        "host": {
            "os": platform.system().lower(),
            "python": platform.python_version(),
            "hostname_hash": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16],
        },
        "env": {"scrubbed": scrub_child_env(os.environ)[1]},  # names only: what the parts did not see
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _shards_ran(parts):
    """True when at least one python shard part was selected and run for this receipt."""
    return any(p.get("name") in _PY_SHARDS for p in parts)


def conclusion_for(parts, shards_ran=True):
    """Check-run conclusion. "failure" when any part is red or there are no parts; "neutral" when
    every part passed but NO python shard ran (a scoped push that owns no test shard has not
    exercised the changed code, so it must not read as a pass); "success" otherwise."""
    if not parts or any(p.get("exit_code") != 0 for p in parts):
        return "failure"
    return "success" if shards_ran else "neutral"


def exit_code_for(parts, args):
    """Exit code the pre-push hook reads (docs/RECEIPT-GATE.md). Red = any part exit != 0.

    0 = green, or red without --fail-on-red (legacy contract: the verifier judges red);
    3 = red and --fail-on-red (the hook blocks the push; the failure receipt is already posted);
    4 = red with --pushed-red (the emergency override was used; the receipt is stamped pushed_red).
    """
    if conclusion_for(parts) != "failure":
        return 0
    if args.pushed_red:
        return 4
    if args.fail_on_red:
        return 3
    return 0


def wrap_receipt_text(envelope):
    """The exact text stored in check-run output.text / the commit comment body."""
    return rc.COMMENT_MARKER + "\n```json\n" + json.dumps(envelope, sort_keys=True) + "\n```\n"


def _default_gh_runner(args):
    res = subprocess.run(["gh"] + list(args), capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    return res.returncode, res.stdout or "", res.stderr or ""


def post_receipt(envelope, slug, gh_runner, spool_dir=None):
    """Publish on the head sha: check-run first, commit comment on 403.

    If commit-comment fails with 422 (commit not found), spool the receipt to
    spool_dir if provided, return "spooled".

    Returns the channel used: "check-run", "commit-comment", or "spooled".
    """
    receipt = envelope["receipt"]
    text = wrap_receipt_text(envelope)
    parts = receipt["parts"]
    # Derived from the receipt itself, so a spooled receipt flushed later (receipt_flush) gets the same verdict.
    conclusion = conclusion_for(parts, shards_ran=_shards_ran(parts))
    summary = "%d part(s) ran, %d skipped; conclusion=%s; scheme=%s" % (
        len(parts), len(receipt.get("skipped", [])), conclusion, envelope["sig"]["scheme"])
    payload = {
        "name": rc.CHECK_NAME, "head_sha": receipt["head_sha"], "status": "completed",
        "conclusion": conclusion,
        "output": {"title": "local receipt (" + conclusion + ")", "summary": summary, "text": text},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        payload_path = fh.name
    try:
        code, out, err = gh_runner(["api", "repos/%s/check-runs" % slug, "-X", "POST", "--input", payload_path])
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass
    if code == 0:
        return "check-run"
    if "403" not in err and "403" not in out:
        raise ReceiptError("check-run POST failed (not a 403): %s" % (err or out).strip()[:300])
    code2, out2, err2 = gh_runner(["api", "repos/%s/commits/%s/comments" % (slug, receipt["head_sha"]),
                                   "-X", "POST", "-f", "body=" + text])
    if code2 == 0:
        return "commit-comment"
    # Check if this is a 422 (commit not found) - spool if possible, otherwise fail
    if "422" in str(code2) or "422" in err2 or "No commit found" in err2:
        if spool_dir:
            # Spool the envelope for later posting
            try:
                spool_path = Path(spool_dir) / (receipt["head_sha"] + ".json")
                spool_path.parent.mkdir(parents=True, exist_ok=True)
                spool_path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
                return "spooled"
            except OSError as e:
                raise ReceiptError("could not write spool file: %s" % e)
        else:
            raise ReceiptError("commit not found on GitHub (sha %s not yet pushed); consider spooling" % receipt["head_sha"][:12])
    raise ReceiptError("check-run POST got 403 and commit-comment fallback failed: %s" % (err2 or out2).strip()[:300])


def choose_scheme(requested, environ):
    if requested != "auto":
        return requested
    # Check for Ed25519 key: explicit arg, env var, or default path
    key_path = rc.resolve_receipt_key_path(environ=environ)
    if rc.ed25519_available() and key_path:
        return "ed25519"
    if environ.get(rc.HMAC_ENV):
        return "hmac-sha256"
    raise ReceiptError("no key material: set %s (Ed25519 PEM path) or %s, or place a key at ~/.aesop/receipt_key.pem" % (rc.KEY_ENV, rc.HMAC_ENV))


def build_parser():
    p = argparse.ArgumentParser(prog="emit_receipt.py", description=__doc__.split("\n")[0])
    p.add_argument("--repo", default=".", help="repo path (default: cwd)")
    p.add_argument("--matrix", default=None, help="comma-separated part names (default: all registered parts)")
    p.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="parallel parts (default: min(4,cpus))")
    p.add_argument("--scheme", default="auto", choices=("auto",) + rc.SCHEMES)
    p.add_argument("--key", default=None, help="Ed25519 private key PEM (default: $%s)" % rc.KEY_ENV)
    p.add_argument("--slug", default=None, help="owner/repo (default: parsed from origin)")
    p.add_argument("--main-ref", default=None,
                   help="trunk ref for base_sha (default: resolved like the pre-push hook: origin/HEAD, "
                        "origin/main, origin/master, else HEAD on a fresh remote; an unresolvable value falls back)")
    p.add_argument("--out", default=None, help="also write the signed envelope JSON here")
    p.add_argument("--post", action="store_true", help="publish as check-run on the head sha (commit comment on 403)")
    p.add_argument("--dry-run", action="store_true", help="print the envelope; perform NO network call even with --post")
    p.add_argument("--fail-on-red", action="store_true",
                   help="exit 3 when any part is red (after the receipt is posted); the pre-push hook blocks on 3")
    p.add_argument("--pushed-red", action="store_true",
                   help="operator override (AESOP_PUSH_RED=1): a red receipt is stamped pushed_red: true and exits 4")
    return p


def main(argv=None, registry=None, gh_runner=None, environ=None):
    environ = os.environ if environ is None else environ
    gh_runner = _default_gh_runner if gh_runner is None else gh_runner
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        # Anchor at the worktree root: a run from a subdirectory must select the same scope.
        repo = Path(_git(repo, "rev-parse", "--show-toplevel"))
        scheme = choose_scheme(args.scheme, environ)
        # Use explicit --key arg, or resolve from env var / default path
        key_path = args.key or rc.resolve_receipt_key_path(environ=environ)
        secret = environ.get(rc.HMAC_ENV)
        if scheme == "ed25519" and not key_path:
            raise ReceiptError("scheme ed25519 needs --key, $%s, or ~/.aesop/receipt_key.pem" % rc.KEY_ENV)
        if scheme == "hmac-sha256" and not secret:
            raise ReceiptError("scheme hmac-sha256 needs $%s" % rc.HMAC_ENV)

        # Determine profile and matrix to run
        profile = _resolve_profile(environ)
        registry = DEFAULT_REGISTRY if registry is None else registry

        scope_skips = []  # shard parts left out by the scoped profile, recorded in the receipt
        trunk = resolve_trunk(repo, args.main_ref)
        if args.matrix:
            # Explicit --matrix overrides profile scoping
            names = [n.strip() for n in args.matrix.split(",") if n.strip()]
        else:
            # Determine matrix based on profile
            if profile == "scoped":
                # Get changed files from git diff
                try:
                    base_sha = _git(repo, "merge-base", "HEAD", trunk)
                    changed_output = _git(repo, "diff", "--name-only", base_sha + "..HEAD")
                    changed_files = [f.strip() for f in changed_output.split("\n") if f.strip()]
                except ReceiptError as exc:
                    # Fail closed: an underivable change set must never narrow the matrix to
                    # nothing (an empty scope passes with zero Python tests run). Run everything.
                    changed_files = None
                    print("scope: cannot derive changed files (%s); running the full matrix (fail closed)"
                          % " ".join(str(exc).split()))

                # Build shard map and find owning shards
                shard_map = _build_shard_map(sorted(_PY_SHARDS), timing=_load_ci_timing(repo), repo=repo)
                if shard_map is None:
                    # Underivable mapping: fall back to full matrix
                    names = list(registry)
                else:
                    owning_shards = None if changed_files is None else _find_owning_shards(changed_files, shard_map)
                    if owning_shards is None:
                        # Underivable: fall back to full
                        names = list(registry)
                    else:
                        # Build scoped matrix
                        names, scoped_skips = _build_parts_and_skipped_for_profile(
                            profile, changed_files, owning_shards, registry
                        )
                        scope_skips = [s for s in scoped_skips if s["name"] in _PY_SHARDS]
            else:  # full
                names = list(DEFAULT_MATRIX)

        parts, skipped = run_matrix(repo, names, registry=registry, jobs=args.jobs)
        skipped = skipped + scope_skips
        receipt = build_receipt(repo, parts, skipped, slug=args.slug, main_ref=trunk, profile=profile)
        if args.post:
            # Recorded before signing: "not posted, and why" is part of the signed record.
            not_posted = non_github_remote(repo)
            if not_posted is not None:
                receipt["posted"] = not_posted
        if args.pushed_red and conclusion_for(parts) == "failure":
            # Stamped BEFORE signing: the override is part of the signed record, not a label beside it.
            receipt["pushed_red"] = True
        sig = rc.sign(rc.canonical_json(receipt), scheme, private_key_path=key_path, hmac_secret=secret)
        envelope = {"receipt": receipt, "sig": sig}
        if args.out:
            Path(args.out).write_text(json.dumps(envelope, sort_keys=True, indent=1), encoding="utf-8")
        for p in parts:
            print("part %-28s exit=%d tests=%s %.1fs" % (p["name"], p["exit_code"], p["test_count"], p["duration_s"]))
            log = part_log_path(repo, receipt["head_sha"], p["name"])
            if p["exit_code"] != 0 and log.is_file():
                print("log  %-28s %s" % (p["name"], log))
        for s in skipped:
            print("skip %-28s %s" % (s["name"], s["reason"]))
        if receipt["env"]["scrubbed"]:
            print("scrub env for parts: %s" % ", ".join(receipt["env"]["scrubbed"]))
        print("conclusion=%s head=%s tree=%s scheme=%s" % (conclusion_for(parts, shards_ran=_shards_ran(parts)),
                                                           receipt["head_sha"][:12],
                                                           receipt["tree_hash"][:12], scheme))
        if args.dry_run:
            print(json.dumps(envelope, sort_keys=True, indent=1))
            print("dry-run: nothing posted")
            return exit_code_for(parts, args)
        if args.post and "posted" in receipt:
            print("WARN: origin is not a github.com remote (%s); check-run not posted, "
                  "receipt records posted.skipped" % receipt["posted"]["remote"])
        elif args.post:
            # Spool directory survives worktree removal via env override or git-common-dir
            spool_dir = rc.resolve_spool_dir(repo=str(repo), environ=environ)
            channel = post_receipt(envelope, receipt["repo"], gh_runner, spool_dir=spool_dir)
            if channel == "spooled":
                print("spooled receipt for %s (publish after push)" % receipt["head_sha"][:12])
            else:
                print("posted via %s on %s as %s" % (channel, receipt["head_sha"][:12], rc.CHECK_NAME))
        if receipt.get("pushed_red"):
            print("pushed_red: true (red receipt stamped; override accepted by caller)")
        return exit_code_for(parts, args)
    except ReceiptError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
