#!/usr/bin/env python3
"""Offline verifier for a signed local receipt (the hosted side of the receipt gate).
INDEX: Receipt-gate increment 2 -- offline verifier used by `.github/workflows/verify-receipt.yml`: input is a receipt envelope (`--receipt FILE|-`, as posted by `emit_receipt.py`) plus the head sha of ITS OWN checkout (`--head`, default HEAD); it RECOMPUTES `git rev-parse <head>^{tree}` and compares to the receipt's tree_hash (never trusts the lane's number), verifies the signature (Ed25519 against the committed `tools/receipt_pubkey.pub`, or HMAC-SHA256 against `$AESOP_RECEIPT_HMAC_SECRET`), checks freshness (receipt.base_sha must be an ancestor of `--main-ref` (origin/main) and at most `--max-behind` (50) commits behind its tip), and requires every REQUIRED part (`--required a,b` ; default py-shard-0..3) present with exit_code 0. Exit 0 = valid, 1 = INVALID (any check failed), 2 = cannot evaluate (unreadable/malformed envelope, no key material for the scheme, git failure) -- fail-closed, never exit 0 on doubt. Also exposes `extract_receipt_from_check_runs()` / `extract_receipt_from_comments()` for the Action's fetch step. stdlib-only; `cryptography` optional (needed for ed25519 receipts).

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
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc  # noqa: E402  (sys.path adjusted above)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


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


def verify(envelope, repo, head, main_ref, max_behind, required, pubkey_path, hmac_secret):
    """Return (exit_code, reasons). 0 valid / 1 invalid / 2 cannot evaluate."""
    try:
        _check_shape(envelope)
        receipt, sig = envelope["receipt"], envelope["sig"]
        scheme = sig.get("scheme")
        reasons = []
        if scheme not in rc.SCHEMES:
            reasons.append("signature: unknown scheme %r" % (scheme,))
        elif not rc.has_key_material(scheme, pubkey_path=pubkey_path, hmac_secret=hmac_secret):
            raise CannotEvaluate("no key material to verify scheme %s (pubkey=%s, hmac=%s)" % (
                scheme, pubkey_path, "set" if hmac_secret else "unset"))
        elif not rc.verify_signature(rc.canonical_json(receipt), sig, pubkey_path=pubkey_path, hmac_secret=hmac_secret):
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


def build_parser():
    p = argparse.ArgumentParser(prog="verify_receipt.py", description=__doc__.split("\n")[0])
    p.add_argument("--receipt", required=True, help="envelope JSON file, or - for stdin")
    p.add_argument("--repo", default=".", help="checkout to verify against (default: cwd)")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--main-ref", default="origin/main")
    p.add_argument("--max-behind", type=int, default=50)
    p.add_argument("--required", default=",".join(rc.DEFAULT_REQUIRED_PARTS), help="comma-separated part names")
    p.add_argument("--pubkey", default=None, help="Ed25519 public key (default: <repo>/%s)" % rc.DEFAULT_PUBKEY_REL)
    p.add_argument("--extract", choices=("check-runs", "comments"), default=None,
                   help="treat --receipt as a GitHub API payload and extract the newest receipt first")
    return p


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    pubkey = Path(args.pubkey) if args.pubkey else repo / rc.DEFAULT_PUBKEY_REL
    try:
        envelope = load_envelope(args.receipt)
        if args.extract == "check-runs":
            envelope = extract_receipt_from_check_runs(envelope)
        elif args.extract == "comments":
            envelope = extract_receipt_from_comments(envelope)
        if envelope is None:
            raise CannotEvaluate("no %s receipt found in the supplied payload" % rc.CHECK_NAME)
    except CannotEvaluate as e:
        print("CANNOT EVALUATE: %s" % e, file=sys.stderr)
        return 2
    required = [n.strip() for n in args.required.split(",") if n.strip()]
    code, reasons = verify(envelope, repo=repo, head=args.head, main_ref=args.main_ref, max_behind=args.max_behind,
                           required=required, pubkey_path=pubkey, hmac_secret=environ.get(rc.HMAC_ENV))
    label = {0: "VALID", 1: "INVALID", 2: "CANNOT EVALUATE"}[code]
    print("receipt %s (head=%s scheme=%s parts=%d)" % (
        label, str(envelope.get("receipt", {}).get("head_sha", "?"))[:12],
        envelope.get("sig", {}).get("scheme"), len(envelope.get("receipt", {}).get("parts", []) or [])))
    for r in reasons:
        print("  - " + r)
    return code


if __name__ == "__main__":
    sys.exit(main())
