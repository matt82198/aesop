#!/usr/bin/env python3
"""Run the local verification matrix and publish a signed receipt bound to HEAD.
INDEX: Receipt-gate increment 1 -- runs the configured local matrix (default: the 4 python shards via `ci_shard_runner.py n 4` plus the pre-push gate set; `--matrix a,b,c` to subset; parts that cannot run on this box, e.g. `browser-proofs`, are recorded as skipped with a reason, never silently dropped), collects `{schema:1, schema_version:1, repo, head_sha, base_sha (merge-base with origin/main), tree_hash (= git rev-parse HEAD^{tree}), parts:[{name, exit_code, test_count, duration_s}], skipped, host:{os, python, hostname_hash}, timestamp}`, canonicalizes (sorted keys, no whitespace) and signs it (`--scheme auto|ed25519|hmac-sha256`; Ed25519 via `cryptography` when importable with the private key at `$AESOP_RECEIPT_KEY`, else HMAC-SHA256 keyed by `$AESOP_RECEIPT_HMAC_SECRET`; the key NEVER lives in the repo), then `--post` publishes it on the head sha as a GitHub check-run named `verify-receipt-local` (conclusion from the parts, receipt JSON in output.text) and, when the token cannot create check-runs (HTTP 403: only GitHub Apps may), falls back to a commit comment carrying the same JSON under a marker; `--dry-run` prints/writes the envelope and performs NO network call. The receipt is NOT a committed file (self-reference + merge conflicts). Exit 0 = receipt produced (parts may still be red: that is what the verifier judges), 2 = could not produce/publish (no key material, unknown part, both publish channels failed). stdlib-only; `cryptography` optional.

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
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc  # noqa: E402  (sys.path adjusted above)

ReceiptError = rc.ReceiptError

_TEST_COUNT_RES = (
    re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE),          # unittest (ci_shard_runner default)
    re.compile(r"\b(\d+) passed\b"),                            # pytest summary
)


class PartSpec:
    """One matrix part: `cmd` builds argv from the repo path; `cmd is None` means not runnable here."""

    def __init__(self, cmd, skip_reason=None):
        self.cmd = cmd
        self.skip_reason = skip_reason


def _py(*rest):
    return lambda repo: [sys.executable] + [str(repo / r) if r.startswith("tools/") else r for r in rest]


DEFAULT_REGISTRY = {
    "py-shard-0": _py("tools/ci_shard_runner.py", "0", "4"),
    "py-shard-1": _py("tools/ci_shard_runner.py", "1", "4"),
    "py-shard-2": _py("tools/ci_shard_runner.py", "2", "4"),
    "py-shard-3": _py("tools/ci_shard_runner.py", "3", "4"),
    "secret-scan": _py("tools/secret_scan.py", "."),
    "claudemd-sync-gate": _py("tools/claudemd_sync_gate.py", "--check"),
    "gen-tool-index": _py("tools/gen_tool_index.py", "--check"),
    "verify-test-suite-count": _py("tools/verify_test_suite_count.py", "--check"),
    "encoding-lint": _py("tools/encoding_lint.py", "--check"),
    "import-resolution-check": _py("tools/import_resolution_check.py"),
    "sibling-import-check": _py("tools/sibling_import_check.py", "--check"),
    "browser-proofs": PartSpec(None, "not runnable here: hosted browsers + playwright matrix (ci.yml browser-proofs)"),
    "windows-shard": PartSpec(None, "not runnable here: hosted windows runner (ci.yml windows-shard)"),
}
for _name, _spec in list(DEFAULT_REGISTRY.items()):
    if not isinstance(_spec, PartSpec):
        DEFAULT_REGISTRY[_name] = PartSpec(_spec)
DEFAULT_MATRIX = [n for n in DEFAULT_REGISTRY]


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


def run_part(repo, name, spec, throwaway_wt=None):
    argv = spec.cmd(Path(throwaway_wt or repo))
    t0 = time.monotonic()
    env = dict(os.environ)
    # Defense in depth: scrub git hook environment variables to prevent matrix parts
    # from accidentally committing to a repo via inherited GIT_DIR/GIT_WORK_TREE.
    # See RECEIPT-GATE.md and hooks/CLAUDE.md for the mechanism.
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        env.pop(var, None)

    with tempfile.TemporaryDirectory(prefix="receipt-%s-" % name) as tmp:
        env["TMPDIR"] = tmp  # per-part temp dir, mirrors ci.yml's shard-specific TMPDIR
        res = subprocess.run(argv, cwd=str(throwaway_wt or repo), env=env, capture_output=True,
                             encoding="utf-8", errors="replace")
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    return {"name": name, "exit_code": int(res.returncode), "test_count": parse_test_count(out),
            "duration_s": round(time.monotonic() - t0, 3)}


def _capture_caller_state(repo):
    """Capture the caller repo's git state for tripwire verification.
    Returns None if repo is not a git repository (e.g., test temp directory)."""
    repo = Path(repo)
    try:
        return {
            "head": _git(repo, "rev-parse", "HEAD"),
            "tree": _git(repo, "write-tree"),
            "porcelain": subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
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


def run_matrix(repo, names, registry=None, jobs=1):
    """Run every runnable part (in parallel up to `jobs`) in a detached throwaway worktree;
    return (parts, skipped). Caller tree is verified untouched via tripwire."""
    registry = DEFAULT_REGISTRY if registry is None else registry
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ReceiptError("unknown matrix part(s): %s (known: %s)" % (", ".join(unknown), ", ".join(sorted(registry))))
    skipped, runnable = [], []
    for n in names:
        spec = registry[n]
        if spec.cmd is None:
            skipped.append({"name": n, "reason": spec.skip_reason or "not runnable here"})
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
            futs = {pool.submit(run_part, repo, n, registry[n], wt): n for n in runnable}
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


def build_receipt(repo, parts, skipped, slug=None, main_ref="origin/main"):
    repo = Path(repo)
    head = _git(repo, "rev-parse", "HEAD")
    return {
        "schema": 1,
        "schema_version": 1,
        "repo": slug or repo_slug(repo) or "unknown",
        "head_sha": head,
        "base_sha": _git(repo, "merge-base", head, main_ref),
        "tree_hash": _git(repo, "rev-parse", head + "^{tree}"),
        "parts": parts,
        "skipped": skipped,
        "host": {
            "os": platform.system().lower(),
            "python": platform.python_version(),
            "hostname_hash": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16],
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def conclusion_for(parts):
    return "success" if parts and all(p.get("exit_code") == 0 for p in parts) else "failure"


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
    summary = "%d part(s) ran, %d skipped; conclusion=%s; scheme=%s" % (
        len(parts), len(receipt.get("skipped", [])), conclusion_for(parts), envelope["sig"]["scheme"])
    payload = {
        "name": rc.CHECK_NAME, "head_sha": receipt["head_sha"], "status": "completed",
        "conclusion": conclusion_for(parts),
        "output": {"title": "local receipt (" + conclusion_for(parts) + ")", "summary": summary, "text": text},
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
    p.add_argument("--main-ref", default="origin/main")
    p.add_argument("--out", default=None, help="also write the signed envelope JSON here")
    p.add_argument("--post", action="store_true", help="publish as check-run on the head sha (commit comment on 403)")
    p.add_argument("--dry-run", action="store_true", help="print the envelope; perform NO network call even with --post")
    return p


def main(argv=None, registry=None, gh_runner=None, environ=None):
    environ = os.environ if environ is None else environ
    gh_runner = _default_gh_runner if gh_runner is None else gh_runner
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        scheme = choose_scheme(args.scheme, environ)
        # Use explicit --key arg, or resolve from env var / default path
        key_path = args.key or rc.resolve_receipt_key_path(environ=environ)
        secret = environ.get(rc.HMAC_ENV)
        if scheme == "ed25519" and not key_path:
            raise ReceiptError("scheme ed25519 needs --key, $%s, or ~/.aesop/receipt_key.pem" % rc.KEY_ENV)
        if scheme == "hmac-sha256" and not secret:
            raise ReceiptError("scheme hmac-sha256 needs $%s" % rc.HMAC_ENV)
        names = [n.strip() for n in args.matrix.split(",") if n.strip()] if args.matrix else list(DEFAULT_MATRIX)
        parts, skipped = run_matrix(repo, names, registry=registry, jobs=args.jobs)
        receipt = build_receipt(repo, parts, skipped, slug=args.slug, main_ref=args.main_ref)
        sig = rc.sign(rc.canonical_json(receipt), scheme, private_key_path=key_path, hmac_secret=secret)
        envelope = {"receipt": receipt, "sig": sig}
        if args.out:
            Path(args.out).write_text(json.dumps(envelope, sort_keys=True, indent=1), encoding="utf-8")
        for p in parts:
            print("part %-28s exit=%d tests=%s %.1fs" % (p["name"], p["exit_code"], p["test_count"], p["duration_s"]))
        for s in skipped:
            print("skip %-28s %s" % (s["name"], s["reason"]))
        print("conclusion=%s head=%s tree=%s scheme=%s" % (conclusion_for(parts), receipt["head_sha"][:12],
                                                           receipt["tree_hash"][:12], scheme))
        if args.dry_run:
            print(json.dumps(envelope, sort_keys=True, indent=1))
            print("dry-run: nothing posted")
            return 0
        if args.post:
            # Spool directory is state/receipts/spool (state is git-ignored)
            spool_dir = repo / "state" / "receipts" / "spool"
            channel = post_receipt(envelope, receipt["repo"], gh_runner, spool_dir=str(spool_dir))
            if channel == "spooled":
                print("spooled receipt for %s (publish after push)" % receipt["head_sha"][:12])
            else:
                print("posted via %s on %s as %s" % (channel, receipt["head_sha"][:12], rc.CHECK_NAME))
        return 0
    except ReceiptError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
