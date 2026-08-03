#!/usr/bin/env python3
r"""Poll GitHub issue comments for remote command dispatch (phone-to-orchestrator).
INDEX: Poll GitHub issue comments for remote command dispatch (phone-to-orchestrator, outbound polling only); verifies repo-owner authorship via GitHub API; strict allowlist of 8 skill commands (/runwave, /power, /afk, etc.); idempotent tracking to prevent replay; appends to the orchestrator state inbox (state/ui-inbox.md) for pickup; posts reply comments for acknowledgment; audit log to state/REMOTE-DISPATCH.log; CLI: `--issue N [--dry-run] [--once]`; exit 0=success / 1=gh failure; designed for scheduled task execution (Windows task scheduler calling --once every 5-10 minutes); documented in docs/REMOTE-ACCESS.md

**SECURITY: Non-negotiable constraints**
- Only comments from the repo owner are accepted; author verified from the API.
- Only fixed allowlist of skill invocations are executed: /runwave, /loopwaves,
  /refinesystem, /refactor, /recency, /highvelocity, /afk, /power.
- Anything else (non-allowlisted commands, free text) is filed as a NOTE, never executed.
- No arbitrary shell, file paths, or code execution. This is a safe remote channel.
- Idempotent: comment IDs tracked to prevent replay of expensive operations.

Usage:
    python tools/remote_inbox.py --issue 123 [--dry-run] [--once]
    python tools/remote_inbox.py --issue 123 --dry-run  # Parse & report, append nothing

Behavior:
    - Polls the GitHub issue for new comments (outbound only, no inbound ports).
    - Verifies author from gh api (author_association == OWNER or user login matches owner).
    - Extracts commands: `/runwave`, `/power`, etc., or free text.
    - Appends to $AESOP_FLEET_STATE_DIR/ui-inbox.md in format: "- [ISO-TS] text".
    - Posts reply comment to acknowledge (executor command or rejected reason).
    - Logs all actions (accepted/rejected) to $AESOP_FLEET_STATE_DIR/REMOTE-DISPATCH.log.
    - Tracks last-seen comment ID to prevent replay on restart.

CLI:
    --issue N       GitHub issue number (required).
    --once          Single poll run (default). Do NOT build a long-running loop here.
    --dry-run       Parse comments, report what would happen, append nothing.
    --help          Show this message.

Exit codes:
    0               Polling succeeded (with or without commands found).
    1               Polling failed (gh api error, malformed response, author check failed).
    2               Usage error (missing --issue, malformed arguments).

Idempotence:
    - Tracks last-seen comment ID in $AESOP_FLEET_STATE_DIR/.remote-inbox-seen.
    - On restart, only new comments (id > last_seen) are processed.
    - Replayed comments are ignored.
"""

import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any


# Allowlisted commands that can be executed
ALLOWED_COMMANDS = {
    "/runwave",
    "/loopwaves",
    "/refinesystem",
    "/refactor",
    "/recency",
    "/highvelocity",
    "/afk",
    "/power",
}

# State location and repo identity are configuration, not constants: this ships in a
# public project, so nothing here may hardcode one operator's home layout or GitHub
# handle. AESOP_FLEET_STATE_DIR points at wherever this deployment keeps fleet state;
# AESOP_REMOTE_REPO ("owner/name") and the repo owner are resolved from git/gh at
# call time so the tool works for whoever actually runs it.
FLEET_STATE = Path(
    os.environ.get("AESOP_FLEET_STATE_DIR", str(Path.cwd() / "state"))
).expanduser()
INBOX_PATH = FLEET_STATE / "ui-inbox.md"
SEEN_PATH = FLEET_STATE / ".remote-inbox-seen"
LOG_PATH = FLEET_STATE / "REMOTE-DISPATCH.log"


def get_repo_slug() -> str:
    """Return "owner/name" for the repo to poll, from env or the gh-resolved remote."""
    slug = os.environ.get("AESOP_REMOTE_REPO", "").strip()
    if slug:
        return slug
    rc, stdout, _ = run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if rc == 0 and stdout.strip():
        return stdout.strip()
    raise RuntimeError(
        "cannot resolve repo: set AESOP_REMOTE_REPO=owner/name or run inside a gh-authenticated repo"
    )


def get_repo_owner() -> str:
    """Return the login authorized to issue remote commands (repo owner by default)."""
    owner = os.environ.get("AESOP_REMOTE_OWNER", "").strip()
    return owner if owner else get_repo_slug().split("/")[0]


def run_gh(args: List[str]) -> Tuple[int, str, str]:
    """Run gh command, return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=False,  # We'll decode ourselves
            timeout=30,
            encoding=None,
        )
        # Decode as UTF-8, fallback to latin-1
        try:
            stdout = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            stdout = result.stdout.decode("latin-1", errors="replace")

        try:
            stderr = result.stderr.decode("utf-8")
        except UnicodeDecodeError:
            stderr = result.stderr.decode("latin-1", errors="replace")

        return result.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        return 1, "", "gh command timed out"
    except FileNotFoundError:
        return 1, "", "gh command not found"
    except Exception as e:
        return 1, "", f"Error running gh: {e}"


def get_issue_comments(issue_number: int) -> Optional[List[Dict[str, Any]]]:
    """Fetch comments from GitHub issue via gh api. Return list of comment dicts or None on error."""
    try:
        slug = get_repo_slug()
    except RuntimeError as e:
        # Contract is "None on error" -- an unresolvable repo is an error, not a crash.
        print(f"ERROR: {e}", file=sys.stderr)
        return None

    rc, stdout, stderr = run_gh(
        ["api", f"repos/{slug}/issues/{issue_number}/comments", "--json", "id,body,author,authorAssociation,createdAt"]
    )

    if rc != 0:
        print(f"ERROR: gh api failed (rc={rc}): {stderr}", file=sys.stderr)
        return None

    try:
        comments = json.loads(stdout)
        return comments if isinstance(comments, list) else []
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse gh response: {e}", file=sys.stderr)
        return None


def read_seen_comments() -> set:
    """Read set of already-processed comment IDs."""
    if not SEEN_PATH.exists():
        return set()

    seen = set()
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line:
                    seen.add(line)
    except Exception:
        pass

    return seen


def mark_comment_seen(comment_id: int) -> None:
    """Append comment ID to seen-file."""
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SEEN_PATH, "a", encoding="utf-8") as f:
            f.write(f"{comment_id}\n")
    except Exception as e:
        print(f"WARNING: Failed to mark comment seen: {e}", file=sys.stderr)


def verify_author(comment: Dict[str, Any], owner_login: Optional[str] = None) -> bool:
    """Verify comment author is repo owner. Check both author_association and login.

    Rejects on anything unverifiable, including an owner that cannot be resolved --
    an unknown owner must never widen who may issue remote commands.
    """
    author = comment.get("author", {})
    if not author:
        return False

    author_login = author.get("login", "")
    author_association = comment.get("authorAssociation", "")

    # Resolve the expected owner only once the comment itself looks well-formed:
    # this needs network/gh, and a malformed comment is rejectable without it.
    if owner_login is None:
        try:
            owner_login = get_repo_owner()
        except RuntimeError as e:
            print(f"ERROR: cannot verify author: {e}", file=sys.stderr)
            return False

    # Must be the owner
    if author_login != owner_login:
        return False

    # author_association should be OWNER for extra safety (but fallback to login check)
    if author_association not in ("OWNER", "COLLABORATOR"):
        return False

    return True


def extract_command(body: str) -> Tuple[Optional[str], str]:
    """Extract command from comment body. Return (command_or_None, clean_text)."""
    body = body.strip()
    if not body:
        return None, ""

    # Look for the first line starting with /
    lines = body.splitlines()
    if lines and lines[0].startswith("/"):
        first_line = lines[0].split()[0].lower()  # Get the command part
        # Check if it's in the allowlist
        if first_line in ALLOWED_COMMANDS:
            return first_line, body
        else:
            # Non-allowlisted command -> treat as NOTE
            return None, body

    # No command, treat as free-text NOTE
    return None, body


def append_inbox(command: Optional[str], text: str) -> None:
    """Append to ui-inbox.md in format: - [ISO-TS] text."""
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ISO 8601 timestamp
    iso_ts = datetime.now(timezone.utc).isoformat()

    # If a command, use it; otherwise use the full text as a NOTE
    if command:
        entry_text = command
    else:
        entry_text = f"NOTE: {text[:100]}"  # Truncate notes to avoid huge entries

    entry = f"- [{iso_ts}] {entry_text}\n"

    try:
        with open(INBOX_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"ERROR: Failed to append to inbox: {e}", file=sys.stderr)
        raise


def post_reply(issue_number: int, comment_id: int, message: str) -> bool:
    """Post a reply to the issue (acknowledge or rejection). Return True on success."""
    reply = f"<!-- remote-inbox reply to comment {comment_id} -->\n{message}"

    rc, _, stderr = run_gh(
        ["issue", "comment", str(issue_number), "-b", reply]
    )

    if rc != 0:
        print(f"WARNING: Failed to post reply: {stderr}", file=sys.stderr)
        return False

    return True


def log_action(action: str, comment_id: int, author: str, command: Optional[str], reason: str = "") -> None:
    """Append action to REMOTE-DISPATCH.log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    iso_ts = datetime.now(timezone.utc).isoformat()
    entry = f"[{iso_ts}] {action:10s} comment={comment_id} author={author} command={command or 'NONE':15s} {reason}\n"

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"WARNING: Failed to write log: {e}", file=sys.stderr)


def process_comments(issue_number: int, dry_run: bool = False) -> bool:
    """Fetch and process comments. Return True on success, False on critical error."""
    comments = get_issue_comments(issue_number)
    if comments is None:
        return False

    seen = read_seen_comments()
    processed_count = 0

    for comment in comments:
        comment_id = comment.get("id")
        if not comment_id:
            continue

        comment_id_str = str(comment_id)
        if comment_id_str in seen:
            print(f"SKIP comment {comment_id} (already processed)", file=sys.stderr)
            continue

        author_info = comment.get("author", {})
        author_login = author_info.get("login", "UNKNOWN")

        # Verify author
        if not verify_author(comment):
            log_action("REJECT", comment_id, author_login, None, "author not owner")
            print(f"REJECT comment {comment_id}: author '{author_login}' not repo owner", file=sys.stderr)
            continue

        body = comment.get("body", "").strip()
        if not body:
            log_action("REJECT", comment_id, author_login, None, "empty body")
            print(f"REJECT comment {comment_id}: empty body", file=sys.stderr)
            continue

        # Extract command or note
        command, text = extract_command(body)

        # Log the action
        if command:
            log_action("ACCEPT", comment_id, author_login, command)
            print(f"ACCEPT comment {comment_id}: command '{command}'", file=sys.stderr)
        else:
            log_action("ACCEPT", comment_id, author_login, None, "filed as NOTE")
            print(f"ACCEPT comment {comment_id}: filed as NOTE", file=sys.stderr)

        # Append to inbox (unless dry-run)
        if not dry_run:
            try:
                append_inbox(command, text)
                mark_comment_seen(comment_id)
                processed_count += 1
            except Exception as e:
                print(f"ERROR: Failed to process comment {comment_id}: {e}", file=sys.stderr)
                return False
        else:
            # Dry-run: report what would happen
            if command:
                print(f"DRY-RUN: Would append command '{command}' to inbox", file=sys.stderr)
            else:
                print(f"DRY-RUN: Would append NOTE to inbox", file=sys.stderr)

    if dry_run:
        print(f"DRY-RUN completed. Processed {len([c for c in comments if str(c.get('id')) not in seen])} comments.", file=sys.stderr)
    else:
        print(f"Processed {processed_count} new comments.", file=sys.stderr)

    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Poll GitHub issue comments for remote command dispatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="GitHub issue number to poll for comments",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report, append nothing",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,  # Default to single run
        help="Single poll run (default)",
    )

    args = parser.parse_args()

    # Validate state directory exists
    if not CONDUCTOR3_STATE.exists():
        print(
            f"ERROR: State directory not found: {CONDUCTOR3_STATE}",
            file=sys.stderr,
        )
        return 1

    # Process comments
    if not process_comments(args.issue, dry_run=args.dry_run):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
