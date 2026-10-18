#!/usr/bin/env python3
"""Offline verifier for a signed local receipt (the hosted side of the receipt gate).
INDEX: Receipt-gate increment 2 -- offline verifier used by `.github/workflows/verify-receipt.yml`: input is a receipt envelope (`--receipt FILE|-`, as posted by `emit_receipt.py`) plus the head sha of ITS OWN checkout (`--head`, default HEAD); it RECOMPUTES `git rev-parse <head>^{tree}` and compares to the receipt's tree_hash (never trusts the lane's number), verifies the signature (Ed25519 against the committed `tools/receipt_pubkey.pub`, or HMAC-SHA256 against `$AESOP_RECEIPT_HMAC_SECRET`), validates `schema_version` (reject if >1; legacy receipts without it still verify for backward compatibility), checks freshness (receipt.base_sha must be an ancestor of `--main-ref` (default: resolved trunk via emit_receipt.resolve_trunk) and at most `--max-behind` (50) commits behind its tip), and requires every REQUIRED part (`--required a,b` ; default py-shard-0..3) present with exit_code 0. Exit 0 = valid, 1 = INVALID (any check failed), 2 = cannot evaluate (unreadable/malformed envelope, no key material for the scheme, git failure) -- fail-closed, never exit 0 on doubt. Also exposes `extract_receipt_from_check_runs()` / `extract_receipt_from_comments()`, and a `--fetch-for-head SHA --repo-slug OWNER/REPO` CLI mode (`fetch_receipt_for_head()`) that the Action's fetch step uses to look up a receipt via `gh api`: FAIL-CLOSED on a missing receipt: no receipt found (or a lookup problem: gh api error, malformed payload) prints `MISSING receipt: ...` and exits 1; a missing `--receipt` file or a payload with no receipt exits 1 the same way. Exit 2 stays for present-but-unevaluable. Lookup never raises. stdlib-only; `cryptography` optional (needed for ed25519 receipts).

What a PASS proves: the holder of the signing key ran the named parts on a tree whose
hash equals the one in this checkout, those parts exited 0, and the run was against a
base no more than N commits stale. What it does NOT prove: that the parts were run
honestly (a key holder can sign anything) -- increment 6 (random hosted re-run +
main-full cross-check) is the honesty mechanism. See docs/RECEIPT-GATE.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc  # noqa: E402  (sys.path adjusted above)
import emit_receipt as emit  # noqa: E402  (shared trunk resolver: the same default the emitter uses)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

# Bounded poll for the fetch step. The pre-push autoflush posts the receipt only after the
# push lands on GitHub, so a single immediate lookup loses the race on fast CI starts.
# Default: keep looking for 5 minutes, every 20 seconds. Hard cap on the window so a bad
# argument cannot turn the Action into an unbounded wait.
DEFAULT_FETCH_WINDOW_S = 300
DEFAULT_FETCH_INTERVAL_S = 20
MAX_FETCH_WINDOW_S = 900


class CannotEvaluate(Exception):
    """Exit-2 class: the verifier cannot reach a verdict (fail-closed)."""


def _git(repo, *args):
    res = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                         encoding="utf-8", errors="replace", timeout=60)
    return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()


def _git_ok(repo, *args):
    code, out, err = _git(repo, *args)
    if code != 0:
        raise CannotEvaluate("git %s failed: %s" % (" ".join(args), err))
    return out


# A GitHub-authored merge head is created server-side. No box ran the gates on its tree,
# so no receipt can honestly exist for it: the MISSING message must say so.
SERVER_MERGE_CAUSE = "server-authored update-branch merge; no box ran the gates; merge main locally and push"
SERVER_COMMITTER_NAME = "GitHub"
SERVER_COMMITTER_EMAIL = "noreply@github.com"


def server_authored_merge(parents, committer_name, committer_email):
    """True for a GitHub-authored merge head: two parents and committer GitHub <noreply@github.com>."""
    return (len(parents) == 2
            and (committer_name or "") == SERVER_COMMITTER_NAME
            and (committer_email or "").strip().lower() == SERVER_COMMITTER_EMAIL)


def _commit_meta_from_git(repo, sha):
    """(parents, committer_name, committer_email) for `sha` from `git cat-file`, or None."""
    code, out, _err = _git(repo, "cat-file", "commit", sha)
    if code != 0:
        return None
    parents, name, email = [], "", ""
    for line in out.splitlines():
        if not line:
            break
        if line.startswith("parent "):
            parents.append(line.split(" ", 1)[1].strip())
        elif line.startswith("committer "):
            m = re.match(r"committer (.*?) <([^>]*)>", line)
            if m:
                name, email = m.group(1), m.group(2)
    return parents, name, email


def _server_merge_suffix(repo, sha):
    """' -- <cause>' when `sha` is a server-authored merge in `repo`, else ''.

    Read from the local object database (`git cat-file`), never from the API: the fetch
    loop calls the API on every poll, and a per-poll commit lookup would multiply those
    calls. The object is the PR head itself, so it is present in the Action's checkout.
    Never raises: an unreadable commit simply names no cause, and the MISSING stays fail-closed.
    """
    try:
        meta = _commit_meta_from_git(repo, sha)
    except Exception:  # noqa: BLE001 -- a failed cause lookup is just no cause, never a crash
        return ""
    if meta and server_authored_merge(*meta):
        return " -- " + SERVER_MERGE_CAUSE
    return ""


def load_envelope(path_or_dash):
    try:
        text = sys.stdin.read() if path_or_dash == "-" else Path(path_or_dash).read_text(encoding="utf-8")
        env = json.loads(text)
    except (OSError, ValueError) as e:
        raise CannotEvaluate("cannot read receipt: %s" % e)
    return env


def _check_shape(envelope):
    if not isinstance(envelope, dict) or not isinstance(envelope.get("receipt"), dict) \
            or not isinstance(envelope.get("sig"), dict):
        raise CannotEvaluate("envelope must be {receipt:{...}, sig:{...}}")
    r = envelope["receipt"]
    if r.get("schema") != 1:
        raise CannotEvaluate("unsupported receipt schema: %r" % (r.get("schema"),))
    for key in ("head_sha", "base_sha", "tree_hash"):
        if not isinstance(r.get(key), str) or not _SHA_RE.match(r[key]):
            raise CannotEvaluate("receipt.%s is not a 40-hex sha" % key)
    if not isinstance(r.get("parts"), list):
        raise CannotEvaluate("receipt.parts must be a list")


def _signature_ok(canonical, sig, pubkey_path, hmac_secret, keyring=None):
    """Ed25519 resolves by key_id across the keyring; HMAC keeps its single-secret check."""
    if sig.get("scheme") == "ed25519":
        return rc.verify_signature_keyring(canonical, sig, rc.keyring_paths(pubkey_path, extra_dir=keyring))
    return rc.verify_signature(canonical, sig, pubkey_path=pubkey_path, hmac_secret=hmac_secret)


def _receipt_changed_files(repo, receipt):
    """Files changed from the receipt's base to its head, or None when git cannot say."""
    code, out, _err = _git(repo, "diff", "--name-only", receipt["base_sha"] + ".." + receipt["head_sha"])
    if code != 0:
        return None
    return [f.strip() for f in out.splitlines() if f.strip()]


def scoped_required_shards(repo, receipt):
    """The python shards a SCOPED receipt must carry, recomputed from its own changed files with
    the emitter's shard assignment (emit_receipt._find_owning_shards over the checkout's shard map).

    Fail closed to EVERY shard when the answer is unknown: an underivable shard map, an
    underivable change set, or an unowned tools/ change (_find_owning_shards -> None). An EMPTY
    owning set is a real answer (docs-only diff, no Python code changed) and requires no py shards,
    matching what the emitter records for a scoped receipt."""
    try:
        shard_map = emit._build_shard_map(sorted(emit._PY_SHARDS), timing=emit._load_ci_timing(repo), repo=repo)
    except Exception:  # noqa: BLE001 -- an underivable map is fail-closed, never a crash
        shard_map = None
    changed = _receipt_changed_files(repo, receipt)
    owning = None
    if changed is not None and shard_map is not None:
        owning = emit._find_owning_shards(changed, shard_map)
    if owning is None:
        return sorted(rc.DEFAULT_REQUIRED_PARTS)
    return sorted(owning)


def effective_required(repo, receipt, required):
    """Required part names for this receipt. A full-profile receipt keeps the caller's list. A scoped
    receipt (profile "scoped", or absent for legacy receipts, or any unknown value) has its shard
    requirement RECOMPUTED from its changed files: the recomputed shards replace the caller's shard
    names, and any non-shard names the caller asked for are kept."""
    if receipt.get("profile") == "full":
        return list(required)
    shards = scoped_required_shards(repo, receipt)
    extra = [n for n in required if n not in emit._PY_SHARDS and n not in shards]
    return shards + extra


def verify(envelope, repo, head, main_ref, max_behind, required, pubkey_path, hmac_secret,
           keyring=None, allow_no_required=False):
    """Return (exit_code, reasons). 0 valid / 1 invalid / 2 cannot evaluate.

    `keyring` is the registered-keys directory to trust (the CI gate passes the BASE tree's).
    An empty `required` list proves nothing, so it is refused (exit 2) unless the caller
    explicitly sets `allow_no_required` (--allow-no-required-parts; CI never does)."""
    try:
        if not required and not allow_no_required:
            raise CannotEvaluate("no required parts: an empty --required proves nothing "
                                 "(pass --allow-no-required-parts only for a deliberate local check)")
        _check_shape(envelope)
        receipt, sig = envelope["receipt"], envelope["sig"]
        scheme = sig.get("scheme")
        reasons = []
        schema_version = receipt.get("schema_version")
        if schema_version is not None and schema_version != 1:
            reasons.append("schema_version: unsupported version %r (expected 1 or absent)" % (schema_version,))
        if scheme not in rc.SCHEMES:
            reasons.append("signature: unknown scheme %r" % (scheme,))
        elif not rc.has_key_material(scheme, pubkey_path=pubkey_path, hmac_secret=hmac_secret):
            raise CannotEvaluate("no key material to verify scheme %s (pubkey=%s, hmac=%s)" % (
                scheme, pubkey_path, "set" if hmac_secret else "unset"))
        elif not _signature_ok(rc.canonical_json(receipt), sig, pubkey_path, hmac_secret, keyring):
            reasons.append("signature: does not verify under %s" % scheme)

        head_full = _git_ok(repo, "rev-parse", "--verify", head + "^{commit}")
        if receipt["head_sha"] != head_full:
            reasons.append("head_sha: receipt %s != checkout %s" % (receipt["head_sha"][:12], head_full[:12]))
        tree = _git_ok(repo, "rev-parse", head_full + "^{tree}")
        if receipt["tree_hash"] != tree:
            reasons.append("tree_hash: receipt %s != recomputed %s" % (receipt["tree_hash"][:12], tree[:12]))

        main_tip = _git_ok(repo, "rev-parse", "--verify", main_ref + "^{commit}")
        code, _o, _e = _git(repo, "merge-base", "--is-ancestor", receipt["base_sha"], main_tip)
        if code == 1:
            reasons.append("base_sha: %s is not an ancestor of %s" % (receipt["base_sha"][:12], main_ref))
        elif code != 0:
            raise CannotEvaluate("git merge-base --is-ancestor failed (unknown base sha?): %s" % _e)
        else:
            behind = int(_git_ok(repo, "rev-list", "--count", receipt["base_sha"] + ".." + main_tip))
            if behind > max_behind:
                reasons.append("base_sha: %d commits behind %s (max %d) -- stale receipt" % (behind, main_ref, max_behind))

        required = effective_required(repo, receipt, required)
        by_name = {}
        for p in receipt["parts"]:
            if isinstance(p, dict) and isinstance(p.get("name"), str):
                by_name[p["name"]] = p
        for name in required:
            p = by_name.get(name)
            if p is None:
                reasons.append("required part missing: %s" % name)
            elif p.get("exit_code") != 0:
                reasons.append("required part %s exit_code=%r" % (name, p.get("exit_code")))
        return (1 if reasons else 0), reasons
    except CannotEvaluate as e:
        return 2, [str(e)]


def _parse_wrapped(text):
    if not isinstance(text, str) or rc.COMMENT_MARKER not in text:
        return None
    m = _FENCE_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def extract_receipt_from_check_runs(payload):
    """Newest `verify-receipt-local` check-run whose output.text wraps a receipt, or None."""
    runs = payload.get("check_runs", []) if isinstance(payload, dict) else payload
    best, best_key = None, ""
    for run in runs or []:
        if not isinstance(run, dict) or run.get("name") != rc.CHECK_NAME:
            continue
        env = _parse_wrapped((run.get("output") or {}).get("text"))
        key = run.get("completed_at") or run.get("started_at") or ""
        if env is not None and key >= best_key:
            best, best_key = env, key
    return best


def extract_receipt_from_comments(comments):
    """Newest commit comment carrying the receipt marker, or None."""
    best, best_key = None, ""
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        env = _parse_wrapped(c.get("body"))
        key = c.get("created_at") or ""
        if env is not None and key >= best_key:
            best, best_key = env, key
    return best


def _gh_api(path):
    res = subprocess.run(["gh", "api", path], capture_output=True, encoding="utf-8",
                         errors="replace", timeout=120)
    if res.returncode != 0:
        print("gh api %s failed: %s" % (path, res.stderr.strip()[:300]), file=sys.stderr)
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        print("gh api %s returned non-JSON" % path, file=sys.stderr)
        return None


def fetch_receipt_for_head(sha, repo_slug, api=None):
    """Look up the newest local receipt for `sha`: check-runs first, commit comments as
    fallback. Returns (envelope_or_None, note).

    This NEVER raises. A failure while merely LOOKING for a receipt (a `gh api` error, a
    malformed payload, anything unexpected) is reported as a MISSING receipt, never as a
    crash. Missing is fail-closed: the caller exits 1 (see `_run_fetch_for_head`), so the
    gap is visible as a red check instead of a green one.
    """
    api = api or _gh_api
    where = "check-runs and commit comments of %s" % repo_slug
    try:
        env = None
        runs = api("repos/%s/commits/%s/check-runs?per_page=100" % (repo_slug, sha))
        if runs is not None:
            env = extract_receipt_from_check_runs(runs)
        if env is None:
            comments = api("repos/%s/commits/%s/comments?per_page=100" % (repo_slug, sha))
            if comments is not None:
                env = extract_receipt_from_comments(comments)
        if env is None:
            return None, "MISSING receipt: no %s receipt on %s (looked in %s)" % (rc.CHECK_NAME, sha, where)
        return env, "receipt found for %s (scheme=%s)" % (sha, env.get("sig", {}).get("scheme"))
    except Exception as e:  # noqa: BLE001 -- defensive: a lookup failure is reported as missing, not a crash
        return None, "MISSING receipt: lookup for %s failed unexpectedly (%s) (looked in %s)" % (sha, e, where)


def poll_receipt_for_head(sha, repo_slug, api=None, *, window_s=DEFAULT_FETCH_WINDOW_S,
                          interval_s=DEFAULT_FETCH_INTERVAL_S, sleep=time.sleep, clock=time.monotonic):
    """Keep looking up the receipt for `sha` until found or `window_s` seconds have passed.

    Lookups run at t=0, then every `interval_s`; the final sleep is clipped to the time left
    in the window, so the total wall time is exactly `window_s` plus the duration of the last
    lookup. A failed lookup is just another miss and does not end the window. Returns the same
    (envelope_or_None, note) pair as fetch_receipt_for_head; None means MISSING after the full
    window. `window_s=0` is a single lookup. `sleep` and `clock` are injectable for tests.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0 (got %r)" % (interval_s,))
    start = clock()
    # Structurally bounded: the loop cannot outlive max_lookups even if the injected clock
    # stalls. The window (clock-driven) is the normal exit; max_lookups is only a backstop.
    max_lookups = max(1, int(window_s // interval_s) + 2)
    lookups = 0
    note = "MISSING receipt: no lookup ran"
    for _ in range(max_lookups):
        env, note = fetch_receipt_for_head(sha, repo_slug, api=api)
        lookups += 1
        if env is not None:
            return env, note
        remaining = window_s - (clock() - start)
        if remaining <= 0:
            break
        sleep(min(interval_s, remaining))
    return None, "%s (polled %d times over a %ds window)" % (note, lookups, window_s)


def build_parser():
    p = argparse.ArgumentParser(prog="verify_receipt.py", description=__doc__.split("\n")[0])
    p.add_argument("--receipt", default=None, help="envelope JSON file, or - for stdin")
    p.add_argument("--repo", default=".", help="checkout to verify against (default: cwd)")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--main-ref", default=None,
                   help="trunk ref for the freshness check (default: resolved by emit_receipt.resolve_trunk, "
                        "the same resolver the emitter uses: origin/HEAD, origin/main, origin/master, else HEAD; "
                        "an explicit value that does not resolve is refused, never substituted)")
    p.add_argument("--max-behind", type=int, default=50)
    p.add_argument("--required", default=",".join(rc.DEFAULT_REQUIRED_PARTS), help="comma-separated part names")
    p.add_argument("--pubkey", default=None, help="Ed25519 public key (default: <repo>/%s)" % rc.DEFAULT_PUBKEY_REL)
    p.add_argument("--keyring", default=None,
                   help="registered-keys dir trusted besides --pubkey (default: <pubkey dir>/receipt_pubkeys). "
                        "The CI gate points this at the BASE tree (origin/main), never the PR head.")
    p.add_argument("--allow-no-required-parts", action="store_true",
                   help="accept an empty --required list (refused by default; CI never passes this)")
    p.add_argument("--extract", choices=("check-runs", "comments"), default=None,
                   help="treat --receipt as a GitHub API payload and extract the newest receipt first")
    p.add_argument("--fetch-for-head", metavar="SHA", default=None,
                   help="look up (don't verify) the newest local receipt for this head sha via `gh api`; "
                        "writes --out-found/--out-envelope and always exits 0 (see fetch_receipt_for_head)")
    p.add_argument("--fetch-window-s", type=int, default=DEFAULT_FETCH_WINDOW_S,
                   help="with --fetch-for-head: keep polling for this many seconds before reporting MISSING "
                        "(default %d, 0 = one lookup, max %d)" % (DEFAULT_FETCH_WINDOW_S, MAX_FETCH_WINDOW_S))
    p.add_argument("--fetch-interval-s", type=int, default=DEFAULT_FETCH_INTERVAL_S,
                   help="with --fetch-for-head: seconds between lookups (default %d, must be >= 1)"
                        % DEFAULT_FETCH_INTERVAL_S)
    p.add_argument("--repo-slug", default=None, help="owner/repo, required with --fetch-for-head")
    p.add_argument("--out-found", default="receipt_found", help="file to write true/false into (--fetch-for-head)")
    p.add_argument("--out-envelope", default="receipt.json", help="file to write the envelope into if found")
    return p


def _run_fetch_for_head(args):
    """CLI body of --fetch-for-head. Exit 0 = a receipt was found (the caller then verifies
    it); exit 1 = MISSING receipt (fail-closed: the gap is a red check, never a green one).
    The out-files are written in every case so the workflow can still report the outcome."""
    try:
        if not args.repo_slug:
            env = None
            note = "MISSING receipt: --repo-slug not given, cannot look up a receipt for %s" % args.fetch_for_head
        else:
            env, note = poll_receipt_for_head(args.fetch_for_head, args.repo_slug,
                                              window_s=args.fetch_window_s, interval_s=args.fetch_interval_s)
        if env is None:
            note += _server_merge_suffix(Path(args.repo).resolve(), args.fetch_for_head)
        print(note)
        Path(args.out_found).write_text("true" if env is not None else "false", encoding="utf-8")
        if env is not None:
            Path(args.out_envelope).write_text(json.dumps(env), encoding="utf-8")
        return 0 if env is not None else 1
    except Exception as e:  # noqa: BLE001 -- defensive: never crash this mode
        print("MISSING receipt: fetch for %s failed unexpectedly (%s)" % (args.fetch_for_head, e))
        Path(args.out_found).write_text("false", encoding="utf-8")
        return 1


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.fetch_window_s <= MAX_FETCH_WINDOW_S:
        parser.error("--fetch-window-s must be between 0 and %d" % MAX_FETCH_WINDOW_S)
    if args.fetch_interval_s < 1:
        parser.error("--fetch-interval-s must be >= 1")
    if args.fetch_for_head:
        return _run_fetch_for_head(args)
    if not args.receipt:
        print("--receipt is required unless --fetch-for-head is given", file=sys.stderr)
        return 2
    repo = Path(args.repo).resolve()
    pubkey = Path(args.pubkey) if args.pubkey else repo / rc.DEFAULT_PUBKEY_REL
    head_label = _git(repo, "rev-parse", "--verify", args.head + "^{commit}")[1] or args.head
    if args.receipt != "-" and not Path(args.receipt).is_file():
        # Missing receipt is fail-closed (exit 1), distinct from an unreadable/malformed one (exit 2).
        print("MISSING receipt: no receipt file %s for head %s (%s)%s" % (
            args.receipt, head_label, rc.CHECK_NAME, _server_merge_suffix(repo, head_label)))
        return 1
    try:
        envelope = load_envelope(args.receipt)
        if args.extract == "check-runs":
            envelope = extract_receipt_from_check_runs(envelope)
        elif args.extract == "comments":
            envelope = extract_receipt_from_comments(envelope)
        if envelope is None:
            print("MISSING receipt: no %s receipt in the supplied payload for head %s%s" % (
                rc.CHECK_NAME, head_label, _server_merge_suffix(repo, head_label)))
            return 1
    except CannotEvaluate as e:
        print("CANNOT EVALUATE: %s" % e, file=sys.stderr)
        return 2
    required = [n.strip() for n in args.required.split(",") if n.strip()]
    keyring = Path(args.keyring) if args.keyring else None
    # An explicit --main-ref is used verbatim (unresolvable -> exit 2); only the default is resolved.
    main_ref = args.main_ref if args.main_ref is not None else emit.resolve_trunk(repo)
    code, reasons = verify(envelope, repo=repo, head=args.head, main_ref=main_ref, max_behind=args.max_behind,
                           required=required, pubkey_path=pubkey, hmac_secret=environ.get(rc.HMAC_ENV),
                           keyring=keyring, allow_no_required=args.allow_no_required_parts)
    label = {0: "VALID", 1: "INVALID", 2: "CANNOT EVALUATE"}[code]
    print("receipt %s (head=%s scheme=%s parts=%d)" % (
        label, str(envelope.get("receipt", {}).get("head_sha", "?"))[:12],
        envelope.get("sig", {}).get("scheme"), len(envelope.get("receipt", {}).get("parts", []) or [])))
    for r in reasons:
        print("  - " + r)
    return code


if __name__ == "__main__":
    sys.exit(main())
