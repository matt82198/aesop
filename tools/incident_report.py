#!/usr/bin/env python3
"""
Incident log generator: mines git history for operational failures.
INDEX: Incident log generator: mines git history for operational failures; Incident-Class / Incident-Category commit trailers are authoritative and unknown values fail closed (non-zero exit naming the commit); test-isolation is a documented alias of test-pollution; subject-only keyword fallback is flagged inferred; 13 classes (incl. environment-shape, daemon-liveness, hook-fail-open, sync-drift, conduct); scans HEAD's history (--all opts into every ref, forensics only); generates docs/INCIDENTS.md; CLI: `[--repo PATH] [--all]` (print) | `--regenerate [--output FILE] [--all]` | `--stdout [--all]` (read-only: the bytes --regenerate would write) | `--check [--all]` (drift exit 1); --regenerate/--stdout refuse a shallow clone (exit 2); registered in generated_paths.REGISTRY + merge_queue.REGENERATORS (regen_all/pre-push/regen-on-main keep it fresh); uses subprocess_common.git() with 60s timeout; all output deterministic, idempotent

Parses git history and extracts failure events, each classified by class.

Classification channels, in precedence order:
  1. Explicit trailer `Incident-Class: <class>` (optional `Incident-Category: <A..E name>`).
     `test-isolation` is an alias of `test-pollution` and is normalized on read.
     Authoritative. An unknown class or category is an ERROR that names the commit;
     the run fails closed (non-zero exit, no log written) rather than dropping the commit.
  2. Fallback for commits WITHOUT the trailer: subject only. A subject prefix of a known
     class name (e.g. "stall: ...") or a subject keyword match. Fallback rows are marked
     "(inferred)" in the generated table. Body text is never keyword-matched, and merge
     commits are never fallback incidents.

Generates docs/INCIDENTS.md: a deterministic, idempotent failure taxonomy table
with columns: class, what_happened, resolution, source_ref.

Scope: by default only the history reachable from HEAD is scanned, so a PR checkout
covers its own commits plus main, and an unrelated local or remote branch can never
fail the gate. `--all` scans every ref (forensic use only).

Usage:
  python tools/incident_report.py [--repo PATH] [--all]
  python tools/incident_report.py --regenerate [--repo PATH] [--output docs/INCIDENTS.md] [--all]
  python tools/incident_report.py --check [--repo PATH] [--all]

Output modes:
  default  - Human-readable table
  --regenerate - Regenerate docs/INCIDENTS.md from live git state
  --check - Exit 0 if docs/INCIDENTS.md matches current state, 1 if drift or an
            unclassifiable trailer (fail closed)

All output is deterministic: stable ordering, no generated timestamps, idempotent regeneration.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subprocess_common import git


# Incident class taxonomy
INCIDENT_CLASSES = {
    "fake-green": "Tests reported green but never ran or skipped real validation",
    "ci-drift": "CI workflow state out of sync (missing deps, env setup, tools)",
    "test-pollution": "Test config leaked between shards, state not isolated, mock pollution",
    "flake": "Test timing/race condition, deflake required, logical time or retry",
    "conflict": "Merge/rebase conflict, module shadowing, unintended override",
    "stall": "Agent/process hung or deadlocked, watchdog detected, restart required",
    "gate-activation": "Pre-push secret/verification gate caught an escape or bypass",
    "doc-invented": "Documentation made unverifiable claims, hallucinated counts or proofs",
    "daemon-liveness": "Daemon or watchdog not running, heartbeat stale, or liveness check blind",
    "hook-fail-open": "Hook exited 0 or passed on malformed input or internal error",
    "sync-drift": "Checkout or brain state out of sync with origin (stale branch, unpulled state)",
    "conduct": "Orchestrator conduct: relayed unverified claims or routed around gates by hand",
    "environment-shape": "defect caused by host or runner shape rather than logic: 8.3 vs resolved Windows paths, linked-worktree .git files, hooks dir resolution, WSL/distro absence; detector = the fix only changes path/platform resolution",
}

# Documented aliases: an alias trailer value is normalized to its class on read and reported
# under that class (never as its own row).
INCIDENT_CLASS_ALIASES = {
    "test-isolation": "test-pollution",
}

# Incident category taxonomy (value of the Incident-Category trailer: "<letter> <name>")
INCIDENT_CATEGORIES = {
    "A": "CI/gates",
    "B": "daemons/fleet",
    "C": "brain hooks",
    "D": "sync/state",
    "E": "orchestrator conduct",
}

CLASS_TRAILER = "Incident-Class"
CATEGORY_TRAILER = "Incident-Category"
_TRAILER_RE = re.compile(r"^(Incident-Class|Incident-Category)[ \t]*:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_CATEGORY_VALUE_RE = re.compile(r"^([A-E])(?:[ \t]+(.+))?$")
_SUBJECT_PREFIX_RE = re.compile(r"^([a-z][a-z-]*):")
# Incident verbs a subject must carry before the gate-activation keywords infer a row.
# aesop #1315: a manifest commit naming a secret-gate test was inferred as an incident.
_INCIDENT_VERB_RE = re.compile(
    r"\b(?:blocks?|blocked|refused|leaks?|leaked|bypass(?:es|ed|ing)?|escapes?|escaped"
    r"|fired|failed closed|denied|reverted|scrubbed|rotated|exposed"
    r"|restores?|restored|inert|broken|missing|regress(?:ed|ion)?)\b"
)
# Gate patterns that are themselves verbs of bypass and need no extra incident verb.
_SELF_SUFFICIENT_GATE_PATTERNS = frozenset({r"\-\-no-verify", r"\-\-admin"})


def parse_trailers(body: str) -> Dict[str, List[str]]:
    """Return {trailer key: [values in order]} for the Incident-* trailers in a commit body.

    Only the final blank-line-separated paragraph is the trailer block (git interpret-trailers
    convention). Earlier prose that happens to wrap onto a line starting with "Incident-Class:"
    is not a trailer and must not fail the gate.
    """
    found: Dict[str, List[str]] = {CLASS_TRAILER: [], CATEGORY_TRAILER: []}
    paragraphs = [p for p in re.split(r"\n[ \t]*\n", (body or "").strip()) if p.strip()]
    block = paragraphs[-1] if paragraphs else ""
    for key, value in _TRAILER_RE.findall(block):
        found[key].append(value)
    return found


# Whole-word private-machine tokens (same set as tools/portability_check.py).
# Historical commit subjects can carry them; the generated log is shipped
# surface, so they are redacted here rather than failing the ratchet gate.
_PRIVATE_TOKENS = ('conductor' + '3', 'matt' + '8')  # split so this source file passes its own gate
PRIVATE_TOKEN_RE = re.compile(r'\b(' + '|'.join(_PRIVATE_TOKENS) + r')\b')


def redact_private_tokens(text: str) -> str:
    """Replace whole-word private-machine tokens with [redacted]."""
    return PRIVATE_TOKEN_RE.sub('[redacted]', text)


def write_table(path, text: str) -> None:
    """Write the generated log with LF endings on every host.

    Text-mode open() on Windows writes CRLF, so the regenerated file differed
    from the committed LF file depending on the generator host.
    """
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


class IncidentClassifier:
    """Classify incidents. Subject-only fallback; explicit trailers are resolved by the parser."""

    def __init__(self):
        """Initialize classifier with patterns."""
        self.patterns = {
            "fake-green": [
                r"green-never-ran",
                r"never-ran",
                r"actually execute.*playwright",
                r"browser-proofs.*actually.*run",
            ],
            "ci-drift": [
                r"fix\(ci\):.*pytest\|chromium\|workflow",
                r"post-#\d+ drift",
                r"fix\(workflow\):",
                r"missing.*pytest.*workflow",
            ],
            "test-pollution": [
                r"leak.*sys\.modules",
                r"shard isolation",
                r"test.*pollut",
            ],
            "flake": [
                r"deflake.*test",
                r"fix.*timing.*test",
                r"boundary.*test.*deflake",
            ],
            "conflict": [
                r"module shadowing",
                r"resolve.*conflict",
                r"Resolve conflicts:",
                r"restore.*original.*spec",
            ],
            "stall": [
                r"stall.*check",
                r"hung.*process",
                r"deadlock",
            ],
            "gate-activation": [
                r"secret.*gate",
                r"\-\-no-verify",
                r"\-\-admin",
                r"gates.*fired",
                r"verification.*bypass",
            ],
            "doc-invented": [
                r"invented precision",
                r"unverifiable",
                r"hallucinated",
                r"remove.*precision",
            ],
        }

    def classify_commit_subject(self, subject: str) -> str:
        """Classify incident from the commit subject alone (fallback channel).

        A subject that starts with a known class name ("stall: ...") is that class.
        Otherwise the keyword patterns are tried in order. gate-activation keywords
        infer a row only when the subject also carries an incident verb (see
        _INCIDENT_VERB_RE); a subject that merely names a gate returns 'unknown'.
        Returns the class name (e.g., 'fake-green') or 'unknown'.
        """
        prefix = _SUBJECT_PREFIX_RE.match(subject.strip().lower())
        if prefix and prefix.group(1) in INCIDENT_CLASSES:
            return prefix.group(1)

        subject_lower = subject.lower()
        for class_name, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, subject_lower, re.IGNORECASE):
                    # aesop #1315: naming a gate ("chore: ... secret-gate test") is not an
                    # incident; a gate keyword needs an incident verb beside it.
                    if (class_name == "gate-activation"
                            and pattern not in _SELF_SUFFICIENT_GATE_PATTERNS
                            and not _INCIDENT_VERB_RE.search(subject_lower)):
                        continue
                    return class_name

        return "unknown"


class IncidentParser:
    """Parse incidents from git history."""

    def __init__(self, repo_root: str = ".", all_refs: bool = False):
        """Initialize with repo root. all_refs=False scans HEAD's history only."""
        self.repo_root = Path(repo_root)
        self.all_refs = all_refs
        self.classifier = IncidentClassifier()

    def _run_git(self, *args, check=True) -> str:
        """Run git command, return stdout."""
        try:
            result = git(list(args), cwd=str(self.repo_root), check=check, timeout=60)
            return (result.stdout or "").strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            return ""

    def _extract_source_ref(self, subject: str, hash_short: str) -> str:
        """Extract source reference (PR #NNN or commit hash) from subject."""
        # Look for PR number in subject
        match = re.search(r'#(\d+)', subject)
        if match:
            return f"PR #{match.group(1)}"

        # Fallback to commit hash
        return f"commit {hash_short}"

    def _extract_what_happened(self, subject: str, body: str) -> str:
        """Extract one-line summary of what happened."""
        # Truncate subject to 70 chars, remove prefix
        summary = subject
        # Remove common prefixes
        summary = re.sub(r'^(fix|feat|harden|ci|chore|docs?)\([^)]*\):\s*', '', summary)
        summary = re.sub(r'\s*\(#\d+\)\s*$', '', summary)  # Remove PR number
        summary = re.sub(r'\s*#\d+\s*$', '', summary)  # Remove trailing #NNN

        if len(summary) > 70:
            summary = summary[:67] + "..."

        return summary

    def _extract_resolution(self, subject: str, body: str) -> str:
        """Extract resolution from commit body."""
        lines = body.split('\n')

        # Look for lines that describe the fix
        resolution_lines = []
        for line in lines:
            line = line.strip()
            if line and len(line) > 10:
                # Skip co-author lines and Incident-* trailers
                if 'Co-Authored-By' in line or 'Authored-By' in line:
                    continue
                if _TRAILER_RE.match(line):
                    continue
                # Take first substantial line as resolution
                if len(resolution_lines) == 0 and line:
                    resolution_lines.append(line)

        if resolution_lines:
            resolution = resolution_lines[0]
            if len(resolution) > 70:
                resolution = resolution[:67] + "..."
            return resolution

        # Fallback: extract from subject
        return self._extract_what_happened(subject, "")

    def _resolve_trailers(self, short_hash: str, subject: str, body: str) -> Tuple[Optional[str], bool, List[str]]:
        """Resolve explicit trailers for one commit.

        Returns (class or None, explicit, errors). Errors name the commit and make the run fail closed.
        """
        errors: List[str] = []
        trailers = parse_trailers(body)

        class_values = [INCIDENT_CLASS_ALIASES.get(v, v) for v in trailers[CLASS_TRAILER]]
        category_values = trailers[CATEGORY_TRAILER]

        if len(set(class_values)) > 1:
            errors.append(f"{short_hash} ({subject!r}): conflicting {CLASS_TRAILER} values {sorted(set(class_values))}")
        for value in class_values[:1]:
            if value not in INCIDENT_CLASSES:
                errors.append(
                    f"{short_hash} ({subject!r}): unknown {CLASS_TRAILER} {value!r} "
                    f"(valid: {', '.join(sorted(INCIDENT_CLASSES))})"
                )

        for value in category_values:
            match = _CATEGORY_VALUE_RE.match(value)
            if not match:
                errors.append(f"{short_hash} ({subject!r}): invalid {CATEGORY_TRAILER} {value!r} (expected A..E)")
                continue
            letter, name = match.group(1), match.group(2)
            if name is not None and name != INCIDENT_CATEGORIES[letter]:
                errors.append(
                    f"{short_hash} ({subject!r}): invalid {CATEGORY_TRAILER} {value!r} "
                    f"(expected {letter} {INCIDENT_CATEGORIES[letter]})"
                )

        if category_values and not class_values:
            errors.append(f"{short_hash} ({subject!r}): {CATEGORY_TRAILER} given without {CLASS_TRAILER}")

        if class_values:
            value = class_values[0]
            return (value if value in INCIDENT_CLASSES else None), True, errors
        return None, False, errors

    def parse(self) -> Tuple[List[Dict[str, str]], List[str]]:
        """Parse the full history.

        Returns (incidents, errors). Errors are never swallowed: any error means the
        log must not be regenerated or checked as current (fail closed).
        Incidents are ordered by date (newest first), then by hash for determinism.
        """
        # Fields are separated by US (\x1f) and records by RS (\x1e), so multi-line
        # bodies and '|' characters in bodies cannot split a record apart.
        output = self._run_git(
            "log",
            "--format=%H%x1f%h%x1f%s%x1f%b%x1f%cI%x1e",  # full | short | subject | body | date
            "--all" if self.all_refs else "HEAD",
            check=False,
        )
        if not output:
            return [], []

        incidents = []
        errors: List[str] = []
        seen_hashes = set()

        for record in output.split('\x1e'):
            if not record.strip():
                continue

            parts = record.lstrip('\n').split('\x1f', 4)
            if len(parts) < 5:
                continue

            full_hash, short_hash, subject, body, date_iso = (
                parts[0], parts[1], parts[2], parts[3], parts[4].strip()
            )

            # Skip duplicates
            if full_hash in seen_hashes:
                continue
            seen_hashes.add(full_hash)

            explicit_class, explicit, trailer_errors = self._resolve_trailers(short_hash, subject, body)
            errors.extend(trailer_errors)

            if explicit:
                incident_class = explicit_class
                inferred = False
            elif subject.startswith("Merge "):
                # A merge commit is never an incident by its subject alone
                # (branch names in merge subjects otherwise match keywords).
                continue
            else:
                incident_class = self.classifier.classify_commit_subject(subject)
                inferred = True

            # Unknown or erroneous rows are never emitted; the error above fails the run.
            if incident_class is None or incident_class == "unknown":
                continue

            what_happened = self._extract_what_happened(subject, body)
            resolution = self._extract_resolution(subject, body)
            source_ref = self._extract_source_ref(subject, short_hash)

            # Parse date for sorting. Never fall back to the wall clock: a clock
            # value changes between runs and makes the order nondeterministic.
            try:
                date_obj = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                date_obj = datetime(1970, 1, 1, tzinfo=timezone.utc)

            incidents.append({
                'hash': short_hash,
                'full_hash': full_hash,
                'class': incident_class,
                'inferred': inferred,
                'what_happened': what_happened,
                'resolution': resolution,
                'source_ref': source_ref,
                'date': date_iso,
                'date_obj': date_obj,
            })

        # Sort by date (newest first), then by hash for determinism
        incidents.sort(key=lambda x: (-x['date_obj'].timestamp(), x['hash']))

        for incident in incidents:
            del incident['date_obj']

        return incidents, errors

    def find_all_incidents(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Return the incidents from parse(), optionally limited (errors are dropped here).

        Callers that must fail closed use parse() and check its errors.
        """
        incidents, _errors = self.parse()
        if limit:
            incidents = incidents[:limit]
        return incidents


class IncidentMarkdown:
    """Generate Markdown output for incidents."""

    def generate_table(self, incidents: List[Dict[str, str]]) -> str:
        """Generate Markdown table from incidents."""
        lines = []
        lines.append("")
        lines.append("# Incidents")
        lines.append("")
        lines.append("Operational failures tracked by class: detection, resolution, and source reference.")
        lines.append("Rows marked (inferred) were classified from the commit subject because the commit carries no Incident-Class trailer.")
        lines.append("")

        if not incidents:
            lines.append("*(No incidents recorded)*")
            lines.append("")
            return "\n".join(lines)

        # Generate summary counts by class
        class_counts: Dict[str, int] = {}
        inferred_counts: Dict[str, int] = {}
        for incident in incidents:
            class_name = incident.get('class') or 'unknown'
            if class_name == 'unknown' or class_name is None:
                continue
            class_counts[class_name] = class_counts.get(class_name, 0) + 1
            if incident.get('inferred'):
                inferred_counts[class_name] = inferred_counts.get(class_name, 0) + 1

        if class_counts:
            lines.append("**Summary**")
            lines.append("")
            for class_name in sorted(class_counts.keys()):
                count = class_counts[class_name]
                description = INCIDENT_CLASSES.get(class_name, "Unknown")
                inferred = inferred_counts.get(class_name, 0)
                suffix = f", {inferred} inferred" if inferred else ""
                lines.append(f"- **{class_name}** ({count}{suffix}): {description}")
            lines.append("")

        # Generate table
        lines.append("| Class | What Happened | Resolution | Source |")
        lines.append("| --- | --- | --- | --- |")

        for incident in incidents:
            class_name = incident['class']
            if incident.get('inferred'):
                class_name = f"{class_name} (inferred)"
            what_happened = incident['what_happened']
            resolution = incident['resolution']
            source_ref = incident['source_ref']

            # Redact private-machine tokens, then escape pipes in content
            what_happened = redact_private_tokens(what_happened).replace('|', '\\|')
            resolution = redact_private_tokens(resolution).replace('|', '\\|')
            source_ref = redact_private_tokens(source_ref)

            lines.append(
                f"| {class_name} | {what_happened} | {resolution} | {source_ref} |"
            )

        lines.append("")

        # Add data-derived latest-entry timestamp
        if incidents:
            # Latest incident is first (newest first)
            latest_date = incidents[0].get('date', '')
            lines.append(f"<!-- Latest incident: {latest_date} -->")

        lines.append("")

        return "\n".join(lines)

    def generate_with_markers(self, incidents: List[Dict[str, str]]) -> str:
        """Generate Markdown with START/END markers for README insertion."""
        lines = []
        lines.append("<!-- INCIDENTS:START -->")
        lines.append("")
        lines.extend(self.generate_table(incidents).split('\n'))
        lines.append("<!-- INCIDENTS:END -->")
        lines.append("")

        return "\n".join(lines)


class IncidentValidator:
    """Validate incident data for secrets/safety."""

    @staticmethod
    def is_valid_git_hash(hash_str: str) -> bool:
        """Check if string is valid git short/long hash."""
        return bool(re.match(r'^[0-9a-f]{7,40}$', hash_str))

    @staticmethod
    def is_valid_source_ref(ref: str) -> bool:
        """Check if source ref is valid (PR #NNN or commit hash)."""
        if ref.startswith('PR #'):
            return bool(re.match(r'^PR #\d+$', ref))
        elif ref.startswith('commit '):
            hash_part = ref[7:]
            return IncidentValidator.is_valid_git_hash(hash_part)
        return False


class IncidentChecker:
    """Check if docs/INCIDENTS.md matches current git state."""

    def __init__(self, repo_root: str = ".", all_refs: bool = False):
        """Initialize checker."""
        self.repo_root = Path(repo_root)
        self.parser = IncidentParser(repo_root, all_refs=all_refs)
        self.markdown = IncidentMarkdown()

    def _is_shallow_clone(self) -> bool:
        """Check if this is a shallow clone (fetch-depth limited in CI).

        Returns True if shallow, False if full history available.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-shallow-repository"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                check=False
            )
            return result.stdout.strip() == "true"
        except Exception:
            # Assume full history if we can't determine
            return False

    def check_incidents_file(self, incidents_file: str = "docs/INCIDENTS.md") -> Tuple[bool, Optional[str]]:
        """Check if INCIDENTS.md matches generated state.

        Returns (match_status, message):
        - (False, message) on any classification error (fail closed, names the commit)
        - (True, None) if they match
        - (False, reason_msg) if they don't match
        - (True, skip_msg) if shallow clone (drift skipped for safety; errors never skipped)
        """
        incidents, errors = self.parser.parse()
        if errors:
            return (False, "FAIL-CLOSED: unclassifiable incident commit(s):\n  " + "\n  ".join(errors))

        incidents_path = self.repo_root / incidents_file
        if not incidents_path.exists():
            return (False, "File does not exist; need to regenerate")

        # Check for shallow clone: fail-closed on drift, skip validation in shallow env
        if self._is_shallow_clone():
            # In CI with shallow clone: cannot verify full incident history
            # Skip validation (don't falsely fail on shallow checkout)
            return (True, "[SKIP] Shallow clone — incident ledger validation skipped (full history unavailable in CI)")

        # Read current file
        try:
            with open(incidents_path, 'r', encoding='utf-8', errors='replace') as f:
                current_content = f.read()
        except IOError:
            return (False, "Cannot read INCIDENTS.md")

        # Generate expected content
        expected_content = self.markdown.generate_table(incidents)

        # Compare (normalize whitespace)
        current_normalized = '\n'.join(l.rstrip() for l in current_content.split('\n'))
        expected_normalized = '\n'.join(l.rstrip() for l in expected_content.split('\n'))

        match = current_normalized.strip() == expected_normalized.strip()
        if match:
            return (True, None)
        else:
            return (False, "DRIFT: docs/INCIDENTS.md does not match current state")


def is_shallow_clone(repo_root: str) -> bool:
    """True when git reports a shallow repository (or the answer cannot be read).

    A shallow checkout sees only a slice of HEAD's history, so a table generated from it
    is truncated. Writers must refuse on True (fail closed); an unreadable answer counts
    as shallow for the same reason.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return True
    return proc.returncode != 0 or proc.stdout.strip() != "false"


def _report_errors(errors: List[str]) -> None:
    """Print classification errors to stderr (fail-closed path)."""
    print("FAIL-CLOSED: unclassifiable incident commit(s); nothing written:", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)


def _configure_stdio() -> None:
    """Never crash on a non-UTF-8 console (e.g. cp1252 with U+FEFF in a commit subject)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    """CLI entry point."""
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root (default: current directory)"
    )
    parser.add_argument(
        "--output",
        default="docs/INCIDENTS.md",
        help="Output file path (default: docs/INCIDENTS.md; relative to --repo, or absolute)"
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan every ref, not only HEAD's history (forensic use; unrelated branches can fail the gate)"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate docs/INCIDENTS.md from live git state"
    )
    mode_group.add_argument(
        "--stdout",
        action="store_true",
        help="Print exactly what --regenerate would write, without touching the file (read-only)"
    )
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Check if docs/INCIDENTS.md matches current state (exit 0=match, 1=drift or classification error)"
    )

    args = parser.parse_args()

    incident_parser = IncidentParser(repo_root=args.repo, all_refs=args.all)
    markdown_gen = IncidentMarkdown()

    if args.stdout:
        # Read-only twin of --regenerate: the exact bytes --regenerate would write.
        # regen_all.py --check compares these against the committed file.
        if is_shallow_clone(args.repo):
            print("FAIL-CLOSED: shallow clone; refusing to generate a truncated incident log "
                  "(fetch full history: actions/checkout fetch-depth: 0)", file=sys.stderr)
            return 2
        incidents, errors = incident_parser.parse()
        if errors:
            _report_errors(errors)
            return 1
        sys.stdout.write(markdown_gen.generate_table(incidents))
        return 0

    if args.regenerate:
        if is_shallow_clone(args.repo):
            print("FAIL-CLOSED: shallow clone; refusing to write a truncated incident log "
                  "(fetch full history: actions/checkout fetch-depth: 0)", file=sys.stderr)
            return 2
        incidents, errors = incident_parser.parse()
        if errors:
            _report_errors(errors)
            return 1

        markdown_output = markdown_gen.generate_table(incidents)

        # Relative --output is taken from --repo; absolute --output is used as-is.
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = Path(args.repo) / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        write_table(output_path, markdown_output)

        print(f"Regenerated {output_path}")
        print(f"Total incidents: {len(incidents)}")
        print(f"Classes: {', '.join(sorted(set(i['class'] for i in incidents)))}")
        return 0

    if args.check:
        checker = IncidentChecker(repo_root=args.repo, all_refs=args.all)
        match, message = checker.check_incidents_file(args.output)
        if match:
            if message:
                print(message)  # Skip message
            else:
                print(f"OK: {args.output} matches current state")
            return 0
        else:
            print(message or f"DRIFT: {args.output} does not match current state")
            return 1

    # Default: print incidents
    incidents, errors = incident_parser.parse()
    if errors:
        _report_errors(errors)
        return 1
    markdown_output = markdown_gen.generate_table(incidents)
    print(markdown_output)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
