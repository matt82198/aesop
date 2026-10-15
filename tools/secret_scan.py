#!/usr/bin/env python3
"""
secret_scan.py — Pre-push secret/credential detection gate.
INDEX: Pre-push secret/credential detection gate (staged/history/paths); token patterns are word-boundary anchored (`\bsk-`) so a key-shaped substring inside a longer hyphenated word is not reported as a live key

Modes:
  secret_scan.py --staged [--repo PATH]              Scan git staged files (default repo=cwd)
  secret_scan.py --range COMMIT_RANGE [--repo PATH]  Scan files changed in range (e.g., main..HEAD or abc123..def456)
  secret_scan.py --history [--repo PATH]             Scan all blobs in git history
  secret_scan.py PATH [PATH...]                      Scan files/dirs directly (recurse dirs)

Exit codes: 0=clean, 1=findings, 2=error (file unreadable/git failure/scan error)
Output: one line per finding or summary (never prints full secrets)

Pragma escape hatch (STRICTLY SCOPED to doc-shaped rules):
  If the literal string 'secretscan: allow-pattern-docs' appears in a file's first 10 lines
  (any comment syntax: #, //, <!-- -->), findings from the DOC-SHAPED rules ONLY
  (env_access only) are reported as ALLOWED-DOC and do not cause exit 1.
  generic_secret_assignment and generic_credential_assignment are NOT pragma-softenable:
  a real-shaped value is fatal regardless; only the rules' own placeholder checks excuse one. Fatal classes (PEM private keys, AWS/GitHub/Slack/OpenAI-Anthropic tokens,
  connection strings) and filename-based findings stay fatal REGARDLESS of the pragma.
  The pragma appears in git diffs and is a reviewable act.

Self-scan invariant: this file must scan CLEAN with NO pragma. Any pattern literal that
would match its own regex is runtime-assembled from fragments (see pem_private_key) so
the pattern text never appears contiguously in this source.

Scan contract (wave-25 P2 fix): --staged and --range scan committed GIT OBJECTS, not the
working-tree copy of changed files. --staged reads each path's STAGED INDEX blob
(`git show :<path>`); --range reads each changed path's blob at the TIP of the range
(`git show <tip>:<path>`). This closes two bypasses that existed when these modes read
$repo/<path> off disk: (1) stage a secret, then edit the on-disk file without
re-staging -- the dirty blob is still what gets committed/pushed; (2) commit a secret,
then edit it away in the worktree without a new commit -- the dirty blob is still what
the pushed commit carries. --history is unaffected (it already walks committed diffs).
Any git command needed to enumerate the files-to-scan that itself fails (bad ref,
unresolvable range, git error) is NOT treated as "zero files changed" -- it raises
GitScanError and the caller fails CLOSED (non-zero exit), so an unresolvable range can
never silently report CLEAN.

NOTE: Public repo version has NO vault allowlist.
"""

import argparse
import os
import re
import sys
import subprocess
from pathlib import Path


# Regex patterns for secret detection (case-insensitive where sensible)
# NOTE: pem_private_key is runtime-assembled from fragments so this source file
# never contains a contiguous PEM-header shape (self-scan invariant, no pragma).
PATTERNS = {
    "pem_private_key": (r"-----BEGIN .* " + "PRIVATE" + " KEY-----", re.IGNORECASE),
    "aws_access_key": (r"AKIA[0-9A-Z]{16}", 0),
    "aws_secret_pattern": (
        r"aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*[^\s\$\<\{]",
        re.IGNORECASE,
    ),
    "github_token": (
        r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}",
        0,
    ),
    "slack_token": (r"xox[baprs]-[A-Za-z0-9-]{10,}", 0),
    # \b prevents matching inside a longer word: "disk-wins-on-any-disagreement"
    # contains sk- followed by 20+ word chars and was flagged as a live key.
    # A real key is always preceded by a non-word char (quote, =, space, start).
    "openai_anthropic_key": (r"\bsk-[A-Za-z0-9_\-]{20,}", 0),
    "generic_secret_assignment": (
        # Unquoted branch requires 8+ contiguous chars from a bare-literal charset
        # (no '.', '(', ')' — real code expressions like `secrets.token_urlsafe(32)`,
        # `self.serve.SESSION_TOKEN`, or `headers.get(...)` all contain one of those)
        # followed by whitespace/EOL, so an assignment from a variable/attribute/call
        # doesn't false-positive as a hardcoded secret the way a bare env-file value
        # (`API_TOKEN=verylongtokenvalue123456789`) legitimately does.
        r"\b(password|passwd|secret|api[_-]?key|token|authorization)\b\s*(?::=|=)\s*(?:[\"'](?!.*(?:xxx|changeme|your-|<|$\{|example)\b).{8,}[\"']|(?!['\"]|xxx|changeme|your-|example)[^\s\$\<\{\n\(\)\.\"']{8,}(?=\s|$))",
        re.IGNORECASE,
    ),
    # F-01 (audit 2026-10-14-r1): KEY_NAME=value / KEY_NAME: value /
    # KEY_NAME = "value" outside .env files (DB_PASSWORD=, API_TOKEN=,
    # STRIPE_SECRET_KEY=). The name must END in a credential word (optionally
    # followed by _KEY), so `token_count = x` is not a name match. The value is
    # checked for placeholders by _credential_value_is_placeholder() (captured
    # value only, never the whole match) and must be >= 8 chars.
    "generic_credential_assignment": (
        r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]*"
        r"(?:PASSWORD|PASSWD|PWD|SECRET|TOKEN|CREDENTIAL|CRED|BEARER"
        r"|API[_\-]?KEY|PRIVATE[_\-]?KEY|ACCESS[_\-]?KEY)"
        r"(?:[_\-]?KEY)?(?![A-Za-z0-9_\-])"
        r"\s*[:=]\s*"
        r"(?:(?P<q>[\"'])(?P<v>[^\"'\n]{8,})(?P=q)"
        r"|(?P<u>[^\s'\"`$<{}()\[\],;:]{8,})(?=\s|$|[;,]))",
        re.IGNORECASE,
    ),
    "connection_string": (
        (r"://"
         r"[^:]+:[^@/\s]+@"
         r"(?!"
            r"localhost(?:[:/]|$)|"
            r"127\.0\.0\.1(?:[:/]|$)|"
            r"127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?:[:/]|$)|"
            r"[a-zA-Z0-9.-]*\.local(?:[:/]|$)|"
            r"[a-zA-Z0-9.-]*\.localdomain(?:[:/]|$)|"
            r"(?:[a-zA-Z0-9-]+\.)*example(?:\.[a-zA-Z]{2,})?(?:[:/]|$)|"
            r"(?:[a-zA-Z0-9-]+\.)*test(?:\.[a-zA-Z]{2,})?(?:[:/]|$)"
         r")"
         r"[^\s]+"),
        0
    ),
    "env_access": (
        r"(?i:(?:os\.getenv|os\.environ|System\.getenv|process\.env)\s*[\[\(][\"']?[A-Z_]*(?:password|secret|api[_-]?key|token|auth|key)[A-Z_0-9]*[\"']?[\)\]])",
        0,
    ),
    "env_assignment": (
        r"(?i:[A-Z_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|PRIVATE|CREDENTIAL|AUTH)[A-Z_0-9]*\s*=\s*)(?!.*(?:xxx|changeme|your-|example))[^\s].{8,}",
        0,
    ),
}

# File patterns that look like credentials (case-insensitive)
CREDENTIAL_FILENAMES = [
    r"\.credentials.*",
    r".*token.*",
    r".*\.pem$",
    r".*\.p12$",
    r"id_rsa.*",
]

# Placeholders that don't count as secrets
PLACEHOLDERS = {"xxx", "changeme", "your-key-here", "example", "test", "demo"}

# Rules that CAN be softened by pragma (doc-shaped rules only)
# generic_credential_assignment and generic_secret_assignment are deliberately NOT
# here: the pragma is self-asserted by the scanned file, so it must never soften a
# real credential-shaped value (F-01 bypass, PR #971 regression; the same bypass for
# `authorization = "<real>"` via generic_secret_assignment). Doc examples are handled
# by each rule's own placeholder check (`<path>`, `<token>`, example, changeme, xxx),
# which runs regardless of the pragma.
SOFTENED_BY_PRAGMA = {"env_access"}

# Rules that are ALWAYS fatal, pragma never applies
FATAL_RULES = {
    "pem_private_key",
    "aws_access_key",
    "github_token",
    "slack_token",
    "openai_anthropic_key",
    "connection_string",
}

# USER-APPROVED EXEMPTION (2026-07-22): redaction-pattern source files.
# A regex whose PURPOSE is redacting connection strings necessarily contains a
# connection-string-SHAPED pattern in its source text. connection_string
# findings in these exact repo-relative paths are downgraded to non-fatal
# ALLOWED-REDACTION-SOURCE (still reported, never silent). Every other rule
# stays fully fatal in these files; connection_string stays fatal everywhere
# else, including pragma'd files. Additions to this set are a security-gate
# change and require explicit user approval.
# Residual risk (accepted): matching is by trailing path components, so a
# nested copy at */bench/sample_transcripts_judgment.py inherits the
# exemption for this ONE rule; all other rules still apply there.
REDACTION_SOURCE_FILES = {"bench/sample_transcripts_judgment.py"}


def _is_redaction_source(scan_path):
    """True when scan_path's trailing components match an exempted file."""
    if not scan_path:
        return False
    parts = str(scan_path).replace("\\", "/").split("/")
    for entry in REDACTION_SOURCE_FILES:
        entry_parts = entry.split("/")
        if parts[-len(entry_parts):] == entry_parts:
            return True
    return False


def _classify_finding(rule_name, has_file_pragma, scan_path=None):
    """Shared fatal/softened decision, used by every scan_* variant (disk-file,
    blob, large-file-chunked) so the pragma contract can't drift between them.
    FATAL_RULES are always fatal — except the single user-approved
    connection_string downgrade for REDACTION_SOURCE_FILES (see above).
    SOFTENED_BY_PRAGMA rules are fatal unless the pragma is present.
    Everything else (e.g. env_assignment) is always fatal."""
    if rule_name == "connection_string" and _is_redaction_source(scan_path):
        return False
    if rule_name in FATAL_RULES:
        return True
    if rule_name in SOFTENED_BY_PRAGMA:
        return not has_file_pragma
    return True


class GitScanError(Exception):
    """Raised when a git command needed to enumerate files-to-scan fails.
    Callers MUST treat this as fail-CLOSED (block / exit non-zero) -- it is
    NOT equivalent to "the range/index genuinely contains zero changed
    files" (wave-25 P2 fix: get_range_files previously returned [] on ANY
    git-diff error, indistinguishable from a real empty range, so main()
    printed CLEAN and exited 0 on an unresolvable range)."""


class ScanError(Exception):
    """Raised when a file cannot be scanned due to read/permission/OS errors.
    Callers MUST treat this as fail-CLOSED (block / exit 2) -- a file the
    scanner cannot read must block the push, not sail through as CLEAN."""


def has_pragma(filepath):
    """Check if file has 'secretscan: allow-pattern-docs' pragma in first 10 lines."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 10:  # Only check first 10 lines
                    break
                if "secretscan: allow-pattern-docs" in line:
                    return True
    except Exception:
        pass
    return False


def is_binary_file(filepath):
    """Check if file is binary (contains null bytes in first 8KB)."""
    try:
        with open(filepath, "rb") as f:
            return b"\x00" in f.read(8192)
    except Exception:
        return True


def should_skip_file(filepath):
    """Check if file should be skipped entirely (.git/, node_modules/, __pycache__, .pyc, .pyo)."""
    # Skip .git directories — match a path COMPONENT, not a substring
    # (".git" in str(path) also matched ".gitignore" and skipped scanning it).
    if ".git" in filepath.parts:
        return True

    # Skip node_modules — third-party deps, always git-ignored, never our code.
    # CI installs them via `npm ci` for the dashboard build, so a whole-tree
    # scan would otherwise walk thousands of package files (README example
    # connection strings, files literally named token.js → false positives).
    if "node_modules" in filepath.parts:
        return True

    # Skip __pycache__ directories
    if "__pycache__" in filepath.parts:
        return True

    # Skip compiled Python artifacts
    if filepath.name.endswith(".pyc") or filepath.name.endswith(".pyo"):
        return True

    return False


def is_placeholder(value):
    """Check if a string is a placeholder value (word-boundary aware)."""
    lower_val = value.lower()
    for p in PLACEHOLDERS:
        if re.search(r'\b' + re.escape(p) + r'\b(?!\.)', lower_val):
            return True
    # Also check for template syntax
    return bool(re.search(r"<[^>]*>|\$\{[^}]*\}", value))


def mask_secret(secret_str):
    """Mask secret: show first 4 chars + *** (or *** if <4 chars)."""
    if len(secret_str) <= 4:
        return "***"
    return f"{secret_str[:4]}***"


def is_env_file(filepath):
    r"""Check if file is .env-like (basename matches ^\.env(\..*)?$ or *.env or *.properties)."""
    name = filepath.name.lower()
    return bool(
        re.match(r"^\.env(\..*)?$", name) or
        name.endswith(".env") or
        name.endswith(".properties")
    )


MAX_SCAN_BYTES = 16 * 1024 * 1024  # F-02: a file over this cap FAILS CLOSED (never skipped)
SIZE_NOTICE_BYTES = 1024 * 1024  # informational SKIPPED-LARGE notice threshold
# Bytes that never appear in text: C0 controls except TAB/LF/CR, and DEL.
_CONTROL_BYTES = bytes([b for b in range(32) if b not in (9, 10, 13)] + [127])


def _credential_value_is_placeholder(value):
    """Placeholder check for generic_credential_assignment. Applied to the
    CAPTURED value only, never the whole match. The placeholder words are
    rule-local on purpose: adding them to PLACEHOLDERS would relax every rule."""
    if not value:
        return True
    low = value.lower()
    for word in ("example", "changeme", "placeholder", "dummy", "xxx", "your-"):
        if word in low:
            return True
    # F-01 follow-up: "..." is a doc ellipsis (`OPENAI_API_KEY=sk-proj-...`), not a
    # literal. Dots are now part of the unquoted value class, so this keeps the
    # widened class from flagging elided doc examples.
    if "..." in value:
        return True
    if re.search(r"<[^>]*>|\$\{[^}]*\}|\$[A-Za-z_]", value):
        return True
    return is_placeholder(value)


def _is_code_identifier(value, rest_of_line):
    """True for a bare identifier or dotted attribute chain (every segment letters/
    underscore only, no digits) that reads as a code or prose reference rather than a
    literal credential: snake_case or camelCase (`api_key: resolved_key`,
    `valid_token=x,`, `token = self.serve.SESSION_TOKEN`), or a word followed by more
    text on the same line (`api_key: word pre-resolved ...`). A value that ends its
    line (`DB_PASSWORD=supersecret`, `DB_PASSWORD=hunter.prod`) stays flagged, as does
    any value with a digit. Dots are accepted in the unquoted value class (F-01
    follow-up) so this guard is what keeps attribute chains from flagging."""
    segments = value.split(".")
    if not all(re.fullmatch(r"[A-Za-z_]+", seg) for seg in segments):
        return False
    if any("_" in seg or re.search(r"[a-z][A-Z]", seg) is not None for seg in segments):
        return True
    return rest_of_line[:1] in (" ", "\t") and rest_of_line.strip() != ""


def _pragma_in_bytes(raw):
    """Same contract as has_pragma(): the marker must appear in the first 10 lines
    of the UTF-8 decode. A UTF-16 file (NUL-interleaved) never matches, as before."""
    head = raw.decode("utf-8", errors="ignore").split("\n")[:10]
    return any("secretscan: allow-pattern-docs" in line for line in head)


def _utf16_no_bom_codec(raw):
    """Detect BOM-less UTF-16 from NUL placement: ASCII-range text encoded as
    UTF-16LE puts a NUL in every odd byte (BE: every even byte). Returns the
    codec name or None."""
    if len(raw) < 4:
        return None
    evens = raw[0::2]
    odds = raw[1::2]
    even_nul = evens.count(0) / len(evens)
    odd_nul = odds.count(0) / len(odds)
    if odd_nul >= 0.3 and even_nul <= 0.1:
        return "utf-16-le"
    if even_nul >= 0.3 and odd_nul <= 0.1:
        return "utf-16-be"
    return None


def _content_views(raw):
    """Decode raw bytes into scan views. Returns (views, nul_tagged) where views
    is a list of (text, full_rules). full_rules=True runs every PATTERNS rule on
    that view; False runs FATAL_RULES only (binary-looking content).

    - UTF-16 BOM: decode by the BOM and run every rule on the decoded text.
    - Text with stray NUL bytes (e.g. a leading NUL): NULs become spaces and every
      rule runs. A single NUL no longer downgrades the file (F-02).
    - BOM-less UTF-16: the UTF-16LE and UTF-16BE decodes at both byte alignments
      run every rule.
    - Binary-looking content (control bytes >= 5%): latin-1 and NUL-stripped
      ASCII views plus UTF-16 views run FATAL_RULES only (provider tokens)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        codec = "utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-16-be"
        return [(raw[2:].decode(codec, errors="replace"), True)], True
    nul_tagged = b"\x00" in raw[:8192]
    sample = raw[:65536].replace(b"\x00", b" ")
    control = len(sample) - len(sample.translate(None, _CONTROL_BYTES))
    if sample and control / len(sample) >= 0.05:
        views = [
            (raw.decode("latin-1"), False),
            (raw.replace(b"\x00", b"").decode("latin-1"), False),
        ]
        for codec in ("utf-16-le", "utf-16-be"):
            for offset in (0, 1):
                views.append((raw[offset:].decode(codec, errors="ignore"), False))
        return views, nul_tagged
    views = [(raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore"), True)]
    codec = _utf16_no_bom_codec(raw)
    if codec:
        for offset in (0, 1):
            views.append((raw[offset:].decode(codec, errors="ignore"), True))
    return views, nul_tagged


def _scan_views(label, views, env_file, has_file_pragma, scan_path):
    """Run PATTERNS over every view. Returns list of (line_num, rule, match_str, is_fatal).
    Duplicate (line, rule, match) hits across views are reported once."""
    findings = []
    seen = set()
    for text, full_rules in views:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line_num, line in enumerate(lines, start=1):
            for rule_name, (pattern, flags) in PATTERNS.items():
                if not full_rules and rule_name not in FATAL_RULES:
                    continue
                if rule_name == "env_assignment" and not env_file:
                    continue
                for match in re.finditer(pattern, line, flags):
                    match_str = match.group(0)
                    if rule_name == "generic_credential_assignment":
                        if match.group("v") is not None:
                            value = match.group("v")
                        else:
                            value = match.group("u")
                            if _is_code_identifier(value, line[match.end():]):
                                continue
                        if _credential_value_is_placeholder(value):
                            continue
                    elif is_placeholder(match_str):
                        continue
                    key = (line_num, rule_name, match_str)
                    if key in seen:
                        continue
                    seen.add(key)
                    is_fatal = _classify_finding(rule_name, has_file_pragma, scan_path)
                    findings.append((line_num, rule_name, match_str, is_fatal))
    return findings


def _credential_filename_findings(filepath):
    filename = filepath.name.lower()
    for pattern in CREDENTIAL_FILENAMES:
        if re.match(pattern, filename, re.IGNORECASE):
            return [(0, "credential_filename", f"File name matches credential pattern: {filepath.name}", True)]
    return []


def _check_scan_cap(label, size):
    """F-02 fail-closed: an over-cap file is refused, never skipped or sampled."""
    if size > MAX_SCAN_BYTES:
        raise ScanError(
            f"{label} is {size} bytes, over the {MAX_SCAN_BYTES}-byte scan cap; "
            "refusing to report CLEAN (fail closed)"
        )


def scan_file(filepath):
    """
    Scan a single file for secrets.
    Returns list of (line_num, rule, match_str, is_fatal).
    is_fatal=True for credential filenames and fatal rule categories;
    is_fatal=False only if pragma present AND rule is in SOFTENED_BY_PRAGMA.

    Every file is scanned in full (see _content_views for how NUL bytes,
    UTF-16 and binary-looking content are decoded). A file over MAX_SCAN_BYTES
    raises ScanError (fail closed) instead of being skipped.

    Raises ScanError if file cannot be read (permission denied, vanished, over cap, etc.)
    to fail CLOSED rather than reporting the file clean.
    """
    if should_skip_file(filepath):
        return []

    findings = _credential_filename_findings(filepath)

    try:
        size = filepath.stat().st_size
    except (OSError, PermissionError, FileNotFoundError) as e:
        raise ScanError(f"Cannot stat {filepath}: {e}")
    _check_scan_cap(filepath, size)
    if size > SIZE_NOTICE_BYTES:
        print(f"SKIPPED-LARGE {filepath} (over 1MB; scanned in full)", file=sys.stderr)

    try:
        raw = filepath.read_bytes()
    except (OSError, PermissionError, FileNotFoundError, IOError) as e:
        raise ScanError(f"Cannot scan file {filepath}: {e}")

    views, nul_tagged = _content_views(raw)
    if nul_tagged:
        print(f"SKIPPED-BINARY {filepath} (NUL bytes present; scanned as text, latin-1 and UTF-16 views)", file=sys.stderr)
    return findings + _scan_views(filepath, views, is_env_file(filepath), _pragma_in_bytes(raw), filepath)


def scan_blob(label, content_bytes):
    """
    Scan raw blob bytes for secrets, reporting findings against `label` as the
    display path. Applies the same rule, fatality and decoding logic as scan_file(),
    on content already fetched from a git object (via get_git_blob) instead of the
    working-tree disk file. Used by --staged (the STAGED INDEX blob) and --range
    (the blob at the TIP of the range) so a secret present only in the git object
    being pushed -- but absent from an out-of-sync working copy -- is not missed
    (wave-25 P2 fix; see module docstring "Scan contract").

    Returns list of (line_num, rule, match_str, is_fatal), like scan_file().
    Raises ScanError when the blob is over MAX_SCAN_BYTES (fail closed).
    """
    filepath = Path(label)
    if should_skip_file(filepath):
        return []

    findings = _credential_filename_findings(filepath)
    _check_scan_cap(label, len(content_bytes))
    if len(content_bytes) > SIZE_NOTICE_BYTES:
        print(f"SKIPPED-LARGE {filepath} (over 1MB; scanned in full from git object)", file=sys.stderr)

    views, nul_tagged = _content_views(content_bytes)
    if nul_tagged:
        print(f"SKIPPED-BINARY {filepath} (NUL bytes present; scanned as text, latin-1 and UTF-16 views)", file=sys.stderr)
    return findings + _scan_views(label, views, is_env_file(filepath), _pragma_in_bytes(content_bytes), label)


def _range_tip_ref(commit_range):
    """Extract the right-hand (tip) ref from a two-dot or three-dot commit
    range string, e.g. 'main..HEAD' -> 'HEAD', 'a...b' -> 'b'. Falls back to
    the whole string if it isn't a recognizable range (single ref/sha)."""
    if "..." in commit_range:
        tip = commit_range.split("...", 1)[1]
    elif ".." in commit_range:
        tip = commit_range.split("..", 1)[1]
    else:
        tip = commit_range
    return tip or "HEAD"


def get_git_blob(repo_path, ref_path):
    """Fetch raw blob bytes via `git show <ref_path>` -- e.g. ref_path=':foo.py'
    reads foo.py from the STAGED INDEX, 'abc123:foo.py' reads foo.py as it
    exists in commit abc123.

    Raises GitScanError if the git show command fails (non-zero returncode or
    exception). Since callers use --diff-filter=d to exclude deleted paths from
    enumeration, a git-show failure for an enumerated path is a real error
    (e.g., corruption, permissions, unreadable blob), not a legitimate 'file
    absent' case. Failing closed (raising GitScanError) ensures such errors
    block the push, consistent with how enumeration failures already fail closed.
    """
    try:
        result = subprocess.run(
            ["git", "show", ref_path],
            cwd=repo_path,
            capture_output=True,
            timeout=15,
        )
    except Exception as e:
        raise GitScanError(
            f"git show {ref_path!r} raised {e!r}"
        )
    if result.returncode != 0:
        raise GitScanError(
            f"git show {ref_path!r} failed (rc={result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='ignore').strip()}"
        )
    return result.stdout


def get_staged_files(repo_path):
    """Get list of staged file paths (relative to repo root) from git repo.

    Raises GitScanError if the underlying `git diff --cached` invocation
    fails; callers must fail CLOSED on that, not treat it as "nothing staged".
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=d"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
        )
    except Exception as e:
        raise GitScanError(f"git diff --cached raised {e!r}")

    if result.returncode != 0:
        raise GitScanError(
            f"git diff --cached failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    return [f for f in result.stdout.strip().split("\n") if f.strip()]


def get_range_files(repo_path, commit_range):
    """Get list of file paths (relative to repo root) changed in commit range
    (e.g. 'main..HEAD' or 'abc123..def456').

    Raises GitScanError if the underlying `git diff` invocation fails (e.g.
    an unresolvable ref); callers must fail CLOSED on that -- it is not the
    same as a range that genuinely touches zero files.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", commit_range],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
        )
    except Exception as e:
        raise GitScanError(f"git diff --name-only {commit_range!r} raised {e!r}")

    if result.returncode != 0:
        raise GitScanError(
            f"git diff --name-only {commit_range!r} failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )

    return [f for f in result.stdout.strip().split("\n") if f.strip()]


def get_history_files(repo_path):
    """Get all file contents from git history via git log -p.

    Raises GitScanError if git log fails (corrupt repo, git crash, etc.).
    This mirrors get_range_files() and get_staged_files() which already
    fail closed on git errors."""
    files_content = []
    try:
        # Use git log -p to get full diff history
        result = subprocess.run(
            ["git", "log", "--all", "-p", "--reverse"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
        )
    except Exception as e:
        raise GitScanError(f"git log raised {e!r}")

    if result.returncode != 0:
        raise GitScanError(
            f"git log failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    # Parse the git log output: each file appears as +++ b/path/to/file followed by its content
    current_file = None
    current_content = []
    for line in result.stdout.split("\n"):
        if line.startswith("+++ b/"):
            if current_file and current_content:
                files_content.append((current_file, "\n".join(current_content)))
            current_file = line[6:]  # Remove "+++ b/"
            current_content = []
        elif current_file and line.startswith("+") and not line.startswith("+++"):
            # Content line (added), strip the leading +
            current_content.append(line[1:])
        elif current_file and line.startswith(" "):
            # Context line, keep it as-is (strip leading space)
            current_content.append(line[1:])
    if current_file and current_content:
        files_content.append((current_file, "\n".join(current_content)))

    return files_content


def _git_committable_set(dir_path):
    """Return the set of resolved absolute paths that git tracks or would
    consider committable (tracked + untracked-but-not-ignored) for the repo
    containing `dir_path`, or None if dir_path is not inside a git work tree
    or git is unavailable/errors.

    Built from `git ls-files --cached --others --exclude-standard`, which lists
    exactly the tracked files plus untracked files that are NOT git-ignored.
    A tracked file is ALWAYS included (even a force-added one that matches an
    ignore rule), so filtering by this set never skips a committable file --
    only ephemeral, git-ignored runtime files (which can never be pushed and
    therefore can't leak a secret through git) are excluded.
    """
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(dir_path), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
        )
        if top.returncode != 0:
            return None
        toplevel = Path(top.stdout.strip())
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=str(toplevel), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
        )
        if listing.returncode != 0:
            return None
    except Exception:
        return None

    committable = set()
    for rel in listing.stdout.split("\0"):
        if rel:
            committable.add((toplevel / rel).resolve())
    return committable


def scan_paths(paths):
    """Recursively scan paths (files and directories).

    For a DIRECTORY argument inside a git work tree, git-ignored files are
    skipped: a full-tree scan (e.g. CI's `secret_scan.py .`) must not flag
    ephemeral, git-ignored runtime files -- such as state/.ui-session-token --
    that can never be committed or pushed and therefore cannot leak a secret
    through git. Filtering uses the tracked+untracked-not-ignored set from
    `git ls-files`, so a committable file is never skipped. When a directory is
    not inside a git repo (or git errors), NO filtering is applied and every
    file is walked (fail OPEN toward more scanning, never less). Explicitly
    named FILE arguments are always scanned, even if git-ignored.
    """
    files_to_scan = []
    for path_str in paths:
        path = Path(path_str).resolve()
        if path.is_file():
            files_to_scan.append(path)
        elif path.is_dir():
            candidates = [p for p in path.rglob("*") if p.is_file()]
            committable = _git_committable_set(path)
            if committable is not None:
                candidates = [p for p in candidates if p.resolve() in committable]
            files_to_scan.extend(candidates)
    return [p for p in files_to_scan if p.is_file()]


def scan_content(content, scan_path=None):
    """Scan raw content for secrets (used by history scanning). scan_path
    (the blob's repo-relative path, when known) feeds the REDACTION_SOURCE_FILES
    exemption so committed redaction-regex source does not turn every future
    --history / prepublish scan permanently red."""
    findings = []
    for line_num, line in enumerate(content.split("\n"), start=1):
        for rule_name, (pattern, flags) in PATTERNS.items():
            matches = re.finditer(pattern, line, flags)
            for match in matches:
                match_str = match.group(0)
                if is_placeholder(match_str):
                    continue
                is_fatal = _classify_finding(rule_name, False, scan_path)
                findings.append((line_num, rule_name, match_str, is_fatal))
    return findings


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan git staged files (requires --repo or uses cwd)",
    )
    parser.add_argument(
        "--range",
        metavar="COMMIT_RANGE",
        help="Scan files changed in commit range (e.g., 'main..HEAD' or 'abc123..def456')",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Scan all blobs in git history (requires --repo or uses cwd)",
    )
    parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read newline-delimited file paths from stdin (scan directly)",
    )
    parser.add_argument(
        "--repo",
        default=os.getcwd(),
        help="Git repo path (default: current directory)",
    )
    parser.add_argument(
        "paths", nargs="*", help="Paths to scan directly (files or directories)"
    )

    args = parser.parse_args()

    # Validate usage: exactly one of --staged, --range, --history, --from-stdin, or paths
    mode_count = sum([args.staged, bool(args.range), args.history, args.from_stdin, bool(args.paths)])
    if mode_count != 1:
        print("ERROR: Use exactly one of --staged, --range, --history, --from-stdin, or path arguments", file=sys.stderr)
        sys.exit(2)

    all_findings = []
    fatal_findings = []
    allowed_doc_count = 0
    allowed_redaction_count = 0
    file_count = 0

    if args.staged:
        # Scan the STAGED INDEX blob for each changed path, NOT the working-tree
        # copy -- a worktree edit made after `git add` must not be able to hide
        # a secret still sitting in the index that would actually get committed.
        try:
            relpaths = get_staged_files(args.repo)
        except GitScanError as e:
            print(f"FATAL: could not determine staged files: {e}", file=sys.stderr)
            print("Failing CLOSED: refusing to report CLEAN when the staged-file listing could not be determined.", file=sys.stderr)
            sys.exit(1)

        file_count = len(relpaths)
        for relpath in relpaths:
            try:
                content = get_git_blob(args.repo, f":{relpath}")
            except GitScanError as e:
                print(f"FATAL: could not read staged blob for {relpath}: {e}", file=sys.stderr)
                print("Failing CLOSED: refusing to report CLEAN when a staged object cannot be read.", file=sys.stderr)
                sys.exit(1)
            label = str(Path(args.repo) / relpath)
            try:
                findings = scan_blob(label, content)
            except ScanError as e:
                print(f"FATAL: cannot scan staged blob {relpath}: {e}", file=sys.stderr)
                sys.exit(1)
            for line_num, rule, match_str, is_fatal in findings:
                all_findings.append((label, line_num, rule, match_str, is_fatal))
                if is_fatal:
                    fatal_findings.append((label, line_num, rule, match_str))
                else:
                    if rule == "connection_string":
                        allowed_redaction_count += 1
                    else:
                        allowed_doc_count += 1

    elif args.range:
        # Scan the COMMITTED blob at the TIP of the range for each changed
        # path, NOT the working-tree copy -- a secret committed then edited
        # away in the worktree (without a new commit) must still be caught,
        # since the pushed commit still carries the dirty blob.
        try:
            relpaths = get_range_files(args.repo, args.range)
        except GitScanError as e:
            print(f"FATAL: could not resolve commit range {args.range!r}: {e}", file=sys.stderr)
            print("Failing CLOSED: refusing to report CLEAN on an unresolvable range.", file=sys.stderr)
            sys.exit(1)

        file_count = len(relpaths)
        tip_ref = _range_tip_ref(args.range)
        for relpath in relpaths:
            try:
                content = get_git_blob(args.repo, f"{tip_ref}:{relpath}")
            except GitScanError as e:
                print(f"FATAL: could not read blob at {tip_ref}:{relpath}: {e}", file=sys.stderr)
                print("Failing CLOSED: refusing to report CLEAN when a committed object cannot be read.", file=sys.stderr)
                sys.exit(1)
            label = str(Path(args.repo) / relpath)
            try:
                findings = scan_blob(label, content)
            except ScanError as e:
                print(f"FATAL: cannot scan blob {tip_ref}:{relpath}: {e}", file=sys.stderr)
                sys.exit(1)
            for line_num, rule, match_str, is_fatal in findings:
                all_findings.append((label, line_num, rule, match_str, is_fatal))
                if is_fatal:
                    fatal_findings.append((label, line_num, rule, match_str))
                else:
                    if rule == "connection_string":
                        allowed_redaction_count += 1
                    else:
                        allowed_doc_count += 1

    elif args.history:
        # History scanning mode: unaffected by the blob-scan fix above, since
        # it already walks committed diff content via `git log -p`.
        try:
            history_files = get_history_files(args.repo)
        except GitScanError as e:
            print(f"FATAL: could not scan git history: {e}", file=sys.stderr)
            print("Failing CLOSED: refusing to report CLEAN when git history cannot be read.", file=sys.stderr)
            sys.exit(2)

        file_count = len(set(f for f, _ in history_files))
        for filepath, content in history_files:
            findings = scan_content(content, scan_path=filepath)
            for line_num, rule, match_str, is_fatal in findings:
                all_findings.append((filepath, line_num, rule, match_str, is_fatal))
                if is_fatal:
                    fatal_findings.append((filepath, line_num, rule, match_str))

    elif args.from_stdin:
        # Read newline-delimited file paths from stdin and scan directly
        paths_from_stdin = []
        for line in sys.stdin:
            line = line.rstrip('\n\r')
            if line:
                paths_from_stdin.append(line)

        files = scan_paths(paths_from_stdin)
        file_count = len(files)
        for filepath in files:
            try:
                findings = scan_file(filepath)
            except ScanError as e:
                print(f"FATAL: cannot scan file {filepath}: {e}", file=sys.stderr)
                print("Failing CLOSED: refusing to report CLEAN when a file cannot be read.", file=sys.stderr)
                sys.exit(2)
            for line_num, rule, match_str, is_fatal in findings:
                all_findings.append((filepath, line_num, rule, match_str, is_fatal))
                if is_fatal:
                    fatal_findings.append((filepath, line_num, rule, match_str))
                else:
                    if rule == "connection_string":
                        allowed_redaction_count += 1
                    else:
                        allowed_doc_count += 1

    else:
        files = scan_paths(args.paths)
        file_count = len(files)
        for filepath in files:
            try:
                findings = scan_file(filepath)
            except ScanError as e:
                print(f"FATAL: cannot scan file {filepath}: {e}", file=sys.stderr)
                print("Failing CLOSED: refusing to report CLEAN when a file cannot be read.", file=sys.stderr)
                sys.exit(2)
            for line_num, rule, match_str, is_fatal in findings:
                all_findings.append((filepath, line_num, rule, match_str, is_fatal))
                if is_fatal:
                    fatal_findings.append((filepath, line_num, rule, match_str))
                else:
                    if rule == "connection_string":
                        allowed_redaction_count += 1
                    else:
                        allowed_doc_count += 1

    # Output findings
    for filepath, line_num, rule, match_str, is_fatal in all_findings:
        masked = mask_secret(match_str)
        if is_fatal:
            print(f"HIGH {filepath}:{line_num} {rule} ({masked})")
        else:
            if rule == "connection_string":
                print(f"ALLOWED-REDACTION-SOURCE {filepath}:{line_num} {rule} ({masked})")
            else:
                print(f"ALLOWED-DOC {filepath}:{line_num} {rule} ({masked})")

    # Summary and exit
    if len(fatal_findings) == 0:
        if allowed_doc_count == 0 and allowed_redaction_count == 0:
            if args.history:
                print(f"CLEAN: git history scanned")
            else:
                print(f"CLEAN: {file_count} files scanned")
        else:
            pragma_file_count = len(set(f for f, _, _, _, is_fatal in all_findings if not is_fatal))
            parts = []
            if allowed_doc_count:
                parts.append(f"{allowed_doc_count} allowed-doc findings in {pragma_file_count} pragma files")
            if allowed_redaction_count:
                parts.append(f"{allowed_redaction_count} allowed-redaction-source findings")
            print(f"CLEAN: scanned ({'; '.join(parts)})")
        sys.exit(0)
    else:
        print(f"FOUND: {len(fatal_findings)} secret(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
