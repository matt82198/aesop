#!/usr/bin/env python3
"""Shared primitives for the receipt gate (canonical JSON + sign/verify).
INDEX: Receipt-gate primitives shared by `emit_receipt.py` and `verify_receipt.py`: `canonical_json()` (sorted keys, no whitespace, ASCII), `sign()`/`verify_signature()` over two schemes -- `ed25519` (preferred; uses `cryptography` ONLY if importable, private key PEM at `$AESOP_RECEIPT_KEY`, public key committed at `tools/receipt_pubkey.pub`) and `hmac-sha256` (stdlib fallback keyed by `$AESOP_RECEIPT_HMAC_SECRET`); `generate_ed25519_keypair()` for operator key rotation; constants `CHECK_NAME`/`COMMENT_MARKER`/`DEFAULT_REQUIRED_PARTS`. Verification is constant-time for HMAC and fails CLOSED (False) on missing key material or an unknown scheme; stdlib-only at import, `cryptography` is an optional runtime import.

Why `.pub` and not `.pem` for the committed public key: tools/secret_scan.py treats
ANY `*.pem` filename as a fatal `credential_filename` finding (and that is the right
default for a repo that must never carry key material), so the public half uses the
ssh convention `.pub`. The content is still a PEM-encoded SubjectPublicKeyInfo.

Why HMAC exists at all: tools/ must stay stdlib-only (tools/CLAUDE.md). Ed25519 is the
target (asymmetric: the verifier holds no secret, so a leaked CI config cannot forge a
receipt), but a box without `cryptography` still needs a way to sign. The verifying
Action installs `cryptography` and accepts both; a receipt names its scheme.
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

CHECK_NAME = "verify-receipt-local"
COMMENT_MARKER = "<!-- aesop-receipt:" + CHECK_NAME + " -->"
DEFAULT_PUBKEY_REL = "tools/receipt_pubkey.pub"
DEFAULT_REQUIRED_PARTS = ("py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3")
KEY_ENV = "AESOP_RECEIPT_KEY"
HMAC_ENV = "AESOP_RECEIPT_HMAC_SECRET"
SPOOL_ENV = "AESOP_RECEIPT_SPOOL"
SCHEMES = ("ed25519", "hmac-sha256")
DEFAULT_KEY_REL = ".aesop/receipt_key.pem"  # Relative to HOME or AESOP_HOME
DEFAULT_SPOOL_REL = ".aesop/receipt-spool"  # Relative to git common dir or HOME


class ReceiptError(Exception):
    """A receipt could not be produced or evaluated (fail-closed, exit 2 at the CLI)."""


def canonical_json(obj) -> bytes:
    """Deterministic encoding: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception:  # pragma: no cover - environment dependent
        return None
    return serialization, ed25519


def ed25519_available() -> bool:
    return _crypto() is not None


def generate_ed25519_keypair(private_path, public_path) -> None:
    """Write a fresh Ed25519 keypair: private PEM (PKCS8) + public PEM (SPKI) as .pub."""
    mods = _crypto()
    if mods is None:
        raise ReceiptError("cryptography is not installed; cannot generate an Ed25519 keypair")
    serialization, ed25519 = mods
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    private_path = Path(private_path)
    private_path.write_bytes(priv)
    try:
        os.chmod(private_path, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    Path(public_path).write_bytes(pub)


def key_id_for_pubkey(pub_pem: bytes) -> str:
    return hashlib.sha256(pub_pem.strip()).hexdigest()[:16]


def sign(canonical: bytes, scheme: str, private_key_path=None, hmac_secret=None) -> dict:
    """Sign canonical bytes. Returns {"scheme", "value" (base64), "key_id"}."""
    if scheme == "ed25519":
        mods = _crypto()
        if mods is None:
            raise ReceiptError("scheme ed25519 requested but cryptography is not importable")
        if not private_key_path or not Path(private_key_path).is_file():
            raise ReceiptError("Ed25519 private key not found (set %s to the PEM path)" % KEY_ENV)
        serialization, _ed = mods
        key = serialization.load_pem_private_key(Path(private_key_path).read_bytes(), password=None)
        pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        value = key.sign(canonical)
        return {"scheme": scheme, "value": base64.b64encode(value).decode("ascii"),
                "key_id": key_id_for_pubkey(pub)}
    if scheme == "hmac-sha256":
        if not hmac_secret:
            raise ReceiptError("scheme hmac-sha256 requested but %s is empty" % HMAC_ENV)
        secret = hmac_secret.encode("utf-8")
        value = hmac.new(secret, canonical, hashlib.sha256).digest()
        return {"scheme": scheme, "value": base64.b64encode(value).decode("ascii"),
                "key_id": hashlib.sha256(secret).hexdigest()[:16]}
    raise ReceiptError("unknown signing scheme: %r (expected one of %s)" % (scheme, ", ".join(SCHEMES)))


def verify_signature(canonical: bytes, sig: dict, pubkey_path=None, hmac_secret=None) -> bool:
    """True only if `sig` is a valid signature of `canonical` under the named scheme.

    Fails closed: unknown scheme, missing key material, malformed base64, or a
    library error all return False (never raise, never pass).
    """
    if not isinstance(sig, dict):
        return False
    scheme = sig.get("scheme")
    try:
        raw = base64.b64decode(sig.get("value", ""), validate=True)
    except Exception:
        return False
    if scheme == "hmac-sha256":
        if not hmac_secret:
            return False
        expect = hmac.new(hmac_secret.encode("utf-8"), canonical, hashlib.sha256).digest()
        return hmac.compare_digest(expect, raw)
    if scheme == "ed25519":
        mods = _crypto()
        if mods is None or not pubkey_path or not Path(pubkey_path).is_file():
            return False
        serialization, ed25519 = mods
        try:
            pub = serialization.load_pem_public_key(Path(pubkey_path).read_bytes())
            if not isinstance(pub, ed25519.Ed25519PublicKey):
                return False
            pub.verify(raw, canonical)
            return True
        except Exception:
            return False
    return False


def has_key_material(scheme: str, pubkey_path=None, hmac_secret=None) -> bool:
    """Whether a verifier COULD evaluate this scheme (distinguishes exit 2 from exit 1)."""
    if scheme == "hmac-sha256":
        return bool(hmac_secret)
    if scheme == "ed25519":
        return ed25519_available() and bool(pubkey_path) and Path(pubkey_path).is_file()
    return False


def resolve_spool_dir(repo=None, environ=None) -> str:
    """Resolve the receipt spool directory path with fallback precedence.

    Priority (highest to lowest):
    1. $AESOP_RECEIPT_SPOOL env var (if set, directory is created if needed)
    2. <git-common-dir>/aesop-receipt-spool (survives worktree removal)
    3. $HOME/.aesop/receipt-spool (home fallback)

    Args:
        repo: Repository path (default: cwd). Used to find git-common-dir.
        environ: Environment dict (default: os.environ).

    Returns the resolved spool directory path as a string. Directory is not created.
    """
    if environ is None:
        environ = os.environ
    if repo is None:
        repo = "."

    # Check for explicit env var first
    if environ.get(SPOOL_ENV):
        return environ[SPOOL_ENV]

    # Try git-common-dir (works inside worktrees)
    repo_path = Path(repo).resolve()
    try:
        git_common = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False
        )
        if git_common.returncode == 0:
            git_common_dir = Path(git_common.stdout.strip()).resolve()
            spool_path = git_common_dir / "aesop-receipt-spool"
            return str(spool_path)
    except Exception:
        pass  # Fall through to home fallback

    # Home fallback
    for home_var in ("HOME",):
        home_dir = environ.get(home_var)
        if home_dir:
            spool_path = Path(home_dir) / DEFAULT_SPOOL_REL
            return str(spool_path)

    # Absolute fallback: use a temp-relative path (unlikely but safe)
    return str(Path.home() / DEFAULT_SPOOL_REL)


def get_spool_search_paths(repo=None, environ=None) -> list:
    """Get all paths to search when flushing receipts (primary + legacy fallback).

    Returns a list of Path objects to check, in priority order:
    1. Resolved primary spool dir
    2. Legacy repo/state/receipts/spool (for backward compatibility)
    """
    if environ is None:
        environ = os.environ
    if repo is None:
        repo = "."

    paths = [Path(resolve_spool_dir(repo=repo, environ=environ))]

    # Add legacy path as fallback source
    repo_path = Path(repo).resolve()
    legacy_path = repo_path / "state" / "receipts" / "spool"
    if legacy_path != paths[0]:  # Avoid duplicates
        paths.append(legacy_path)

    return paths


def resolve_receipt_key_path(environ=None) -> str:
    """Resolve the Ed25519 private key path with fallback to default location.

    Priority (highest to lowest):
    1. $AESOP_RECEIPT_KEY env var (if set, file must exist)
    2. $AESOP_HOME/.aesop/receipt_key.pem (if AESOP_HOME set and file exists)
    3. $HOME/.aesop/receipt_key.pem (if HOME set and file exists)

    Returns the path string if found, or None if no valid key location exists.
    This allows shells that don't inherit AESOP_RECEIPT_KEY to still find the key.
    """
    if environ is None:
        environ = os.environ

    # Check for explicit env var first
    if environ.get(KEY_ENV):
        return environ[KEY_ENV]

    # Try AESOP_HOME override, then HOME fallback
    for home_var in ("AESOP_HOME", "HOME"):
        home_dir = environ.get(home_var)
        if home_dir:
            default_key = Path(home_dir) / DEFAULT_KEY_REL
            if default_key.is_file():
                return str(default_key)

    return None
