#!/usr/bin/env python3
"""Sign a file's sha256 as a provenance receipt, and verify such a receipt offline.
INDEX: Provenance receipt -- signs {schema, kind, repo, version, subject:{path, sha256}} with the Ed25519 key at $AESOP_RECEIPT_KEY (or the default ~/.aesop/receipt_key.pem) and writes the envelope {receipt, sig}; `--verify ENVELOPE --subject FILE` recomputes the subject sha256 and checks the signature against the registered keys in tools/receipt_pubkeys. Exit 0 = emitted or VALID, 1 = INVALID, 2 = cannot emit or evaluate. Stdlib plus receipt_common only; `cryptography` is needed to sign or verify.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc  # noqa: E402  (sys.path adjusted above)

KIND = "provenance"
DEFAULT_KEYRING = _TOOLS_DIR / "receipt_pubkeys"


def subject_sha256(path):
    """Hex sha256 of the subject file with CRLF folded to LF.

    Git with core.autocrlf rewrites LF checkouts to CRLF on Windows; folding keeps the digest
    the same on every checkout of the same commit.
    """
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def build_receipt(subject_path, subject_rel, version, repo):
    """The unsigned receipt body. Signed as canonical JSON, so every field is covered."""
    return {
        "schema": 1,
        "kind": KIND,
        "repo": repo,
        "version": version,
        "subject": {"path": subject_rel, "sha256": subject_sha256(subject_path)},
    }


def emit(subject_path, subject_rel, version, repo, key_path, out_path):
    """Sign the receipt with the Ed25519 key and write the envelope to out_path."""
    receipt = build_receipt(subject_path, subject_rel, version, repo)
    sig = rc.sign(rc.canonical_json(receipt), "ed25519", private_key_path=key_path)
    envelope = {"receipt": receipt, "sig": sig}
    Path(out_path).write_text(json.dumps(envelope, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return envelope


def verify(envelope, subject_path, keyring_dir):
    """Return (exit_code, reasons): 0 VALID, 1 INVALID, 2 cannot evaluate. Fails closed."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get("receipt"), dict) \
            or not isinstance(envelope.get("sig"), dict):
        return 2, ["envelope must be {receipt:{...}, sig:{...}}"]
    receipt, sig = envelope["receipt"], envelope["sig"]
    if receipt.get("schema") != 1 or receipt.get("kind") != KIND:
        return 2, ["unsupported receipt: schema=%r kind=%r" % (receipt.get("schema"), receipt.get("kind"))]
    if not Path(subject_path).is_file():
        return 2, ["subject file not found: %s" % subject_path]
    paths = sorted(p for p in Path(keyring_dir).glob("*.pub") if p.is_file())
    if not paths:
        return 2, ["no registered public keys in %s" % keyring_dir]
    if not rc.ed25519_available():
        return 2, ["cryptography is not importable; cannot verify an ed25519 signature"]

    reasons = []
    subject = receipt.get("subject") or {}
    if subject.get("sha256") != subject_sha256(subject_path):
        reasons.append("subject sha256: receipt %s != file %s" % (subject.get("sha256"), subject_sha256(subject_path)))
    if not rc.verify_signature_keyring(rc.canonical_json(receipt), sig, paths):
        reasons.append("signature: does not verify under any registered key")
    return (1 if reasons else 0), reasons


def build_parser():
    p = argparse.ArgumentParser(prog="emit_provenance_receipt.py", description=__doc__.split("\n")[0])
    p.add_argument("--subject", required=True, help="file whose exact bytes are signed")
    p.add_argument("--subject-path", default=None, help="repo-relative path recorded in the receipt (default: --subject)")
    p.add_argument("--version", default=None, help="release version recorded in the receipt (emit mode)")
    p.add_argument("--repo", default="matt82198/aesop", help="owner/repo recorded in the receipt")
    p.add_argument("--out", default=None, help="envelope output path (emit mode)")
    p.add_argument("--verify", metavar="ENVELOPE", default=None, help="verify this envelope instead of emitting")
    p.add_argument("--keyring", default=str(DEFAULT_KEYRING), help="directory of registered *.pub keys")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    subject_rel = args.subject_path or args.subject
    if args.verify:
        try:
            envelope = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print("CANNOT EVALUATE: cannot read envelope: %s" % e, file=sys.stderr)
            return 2
        code, reasons = verify(envelope, args.subject, args.keyring)
        label = {0: "VALID", 1: "INVALID", 2: "CANNOT EVALUATE"}[code]
        print("provenance receipt %s (subject=%s)" % (label, subject_rel))
        for r in reasons:
            print("  - " + r)
        return code
    if not args.version or not args.out:
        print("emit mode needs --version and --out", file=sys.stderr)
        return 2
    try:
        key_path = rc.resolve_receipt_key_path()
        if not key_path:
            print("CANNOT EMIT: no Ed25519 key; set %s to the PEM path" % rc.KEY_ENV, file=sys.stderr)
            return 2
        emit(args.subject, subject_rel, args.version, args.repo, key_path, args.out)
    except (rc.ReceiptError, OSError) as e:
        print("CANNOT EMIT: %s" % e, file=sys.stderr)
        return 2
    print("provenance receipt emitted: %s (subject=%s version=%s)" % (args.out, subject_rel, args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
