#!/usr/bin/env python3
"""
Project initialization scaffolder for aesop.
INDEX: Project scaffolder (`aesop init`): creates CLAUDE.md, config, state dir, CI template, pre-push hook, and copies secret_scan.py

When a user runs `npx @matt82198/aesop init` in a new repo, this tool
scaffolds the aesop orchestration layer:

1. Root CLAUDE.md with project purpose placeholder, domain map, dispatch rule
2. A starter domain CLAUDE.md for the first discovered code directory
3. aesop.config.json with sensible defaults
4. state/ directory with .gitkeep
5. .github/workflows/ci.yml minimal CI template
6. tools/secret_scan.py (copied from aesop repo, fail-closed gate)
7. Git pre-push hook (secret scan gate with fail-closed logic)
8. Prints a "Getting Started" summary

CLI: python tools/init_project.py [--dir PATH] [--name PROJECT_NAME] [--force]
Exit: 0=success, 1=error, 2=usage error
"""
import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


# --- Templates -----------------------------------------------------------

ROOT_CLAUDE_MD = """\
# {project_name} -- Project CLAUDE.md

**What**: {project_name} is a software project orchestrated by aesop multi-agent dispatch.

## Domain map

{domain_map}

## Dispatch rule

Workers read exactly ONE domain CLAUDE.md; this file is navigation only.

## Setup for development

1. Install dependencies per your stack.
2. Run `npx @matt82198/aesop doctor` to verify readiness.
3. Launch the dashboard: `npx @matt82198/aesop dash`
"""

DOMAIN_CLAUDE_MD = """\
# {domain_name}/ -- Domain CLAUDE.md

**Purpose**: {domain_name} source code.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by secret scan.
- Tests never pollute cwd or global git config; temp dirs only.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Key files

- (Add key files for this domain here)

## Test commands

- (Add test commands for this domain here)

---
Map of all domains: /CLAUDE.md
"""

DEFAULT_CONFIG = {
    "description": "Aesop orchestration configuration.",
    "state_root": "./state",
    "dashboard": {
        "port": 8770
    },
    "identity": {
        "name": None,
        "email": None
    },
    "cardinal_rules": {
        "subagent_model": "haiku",
        "tdd_first": True,
        "never_push_main": True,
        "secret_scan_gates_push": True
    }
}

CI_YML = """\
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: echo "Add your test commands here"
"""

PRE_PUSH_HOOK = """\
#!/usr/bin/env bash
set -uo pipefail

# Aesop pre-push hook: secret scan gate
# Installed by tools/init_project.py

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$repo_root" ]; then
  echo "ERROR: Not inside a git repository."
  exit 1
fi

# Branch protection: block pushes to main/master
current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$current_branch" = "main" ] || [ "$current_branch" = "master" ]; then
  echo "ERROR: Direct push to $current_branch is blocked by policy."
  echo "Use a feature branch and open a pull request."
  exit 1
fi

# Secret scan gate (fail-closed: exit 1 if script is missing)
scan_script="$repo_root/tools/secret_scan.py"
if [ -f "$scan_script" ]; then
  python3 "$scan_script" --staged --repo "$repo_root"
  scan_exit=$?
  if [ $scan_exit -ne 0 ]; then
    echo "ERROR: Secret scan found issues. Push blocked."
    exit 1
  fi
else
  echo "ERROR: Secret scan script not found at $scan_script"
  echo "The pre-push hook cannot run. Push blocked."
  echo "This is likely a setup error: tools/secret_scan.py must be present in the repo."
  exit 1
fi

exit 0
"""

# --- Well-known code directories -----------------------------------------

CODE_DIRS = [
    "src", "lib", "app", "apps", "packages", "pkg",
    "server", "client", "api", "core", "internal", "cmd",
]


# --- Core logic ----------------------------------------------------------

def detect_project_name(target_dir):
    """Derive a project name from the directory or git remote."""
    target = Path(target_dir).resolve()

    # Try git remote origin
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(target), timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            # Extract repo name from URL
            name = url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fall back to directory name
    return target.name or "my-project"


def detect_git_identity(target_dir):
    """Read git user.name and user.email from the repo config."""
    identity = {"name": None, "email": None}
    target = str(Path(target_dir).resolve())
    for key, field in [("user.name", "name"), ("user.email", "email")]:
        try:
            result = subprocess.run(
                ["git", "config", key],
                capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=target, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                identity[field] = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return identity


def discover_code_dirs(target_dir):
    """Return list of well-known code directories that exist in target."""
    target = Path(target_dir).resolve()
    found = []
    for d in CODE_DIRS:
        candidate = target / d
        if candidate.is_dir() and not candidate.is_symlink():
            found.append(d)
    return found


def write_file(filepath, content, force=False):
    """Write a file, creating parent directories. Skip if exists and not force."""
    p = Path(filepath)
    if p.exists() and not force:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return True


def build_domain_map(domains):
    """Build the domain map section for the root CLAUDE.md."""
    if not domains:
        return "- **(no code directories detected yet)** -- add your domains here"
    lines = []
    for d in domains:
        lines.append(
            f"- **{d}/** -- {d} source code -- read {d}/CLAUDE.md"
        )
    return "\n".join(lines)


def resolve_real_git_dir(target_dir):
    """
    Resolve real git directory, handling worktree case.
    In a worktree, .git is a FILE containing 'gitdir: <path>'.
    Returns Path to the actual git directory, or None if resolution fails.
    """
    target = Path(target_dir).resolve()
    git_path = target / ".git"

    # Check if .git exists
    if not git_path.exists():
        return None

    # Check if .git is a file (worktree case)
    if git_path.is_file():
        # Worktree case: use git rev-parse --git-common-dir to get hooks dir
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(target),
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = result.stdout.strip()
                # Resolve relative paths
                if Path(common_dir).is_absolute():
                    return Path(common_dir)
                else:
                    return (target / common_dir).resolve()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Fallback: parse gitdir pointer manually
        try:
            content = git_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("gitdir:"):
                    gitdir_path = line.split(":", 1)[1].strip()
                    if gitdir_path:
                        p = Path(gitdir_path)
                        if not p.is_absolute():
                            p = (target / gitdir_path).resolve()
                        return p
        except (OSError, UnicodeDecodeError):
            pass

        return None
    elif git_path.is_dir():
        # Regular git directory
        return git_path
    else:
        # .git exists but is neither file nor directory (symlink/other)
        return None


def copy_secret_scan_script(target_dir):
    """
    Copy tools/secret_scan.py from the aesop repo into the target repo's tools/ dir.
    Skips if already present.

    Returns: (success: bool, message: str)
    """
    target = Path(target_dir).resolve()
    target_tools_dir = target / "tools"
    target_scan_path = target_tools_dir / "secret_scan.py"

    # Find the source secret_scan.py (in the aesop repo)
    try:
        aesop_repo_root = Path(__file__).parent.parent.resolve()
        source_scan_path = aesop_repo_root / "tools" / "secret_scan.py"

        if not source_scan_path.is_file():
            return False, f"source secret_scan.py not found at {source_scan_path}"

        # Skip if already exists
        if target_scan_path.exists():
            return False, "already exists"

        # Create target tools directory
        target_tools_dir.mkdir(parents=True, exist_ok=True)

        # Copy the file
        source_content = source_scan_path.read_text(encoding="utf-8")
        target_scan_path.write_text(source_content, encoding="utf-8")

        # Make executable (no-op on Windows, matters on POSIX)
        try:
            target_scan_path.chmod(target_scan_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass

        return True, "copied"
    except Exception as e:
        return False, f"failed to copy: {e}"


def install_pre_push_hook(target_dir, force=False):
    """Install the pre-push hook into .git/hooks/."""
    target = Path(target_dir).resolve()

    # Resolve real git directory (handles worktree case)
    git_dir = resolve_real_git_dir(str(target))
    if not git_dir:
        return False, "no .git directory or resolution failed"

    # Security: reject symlinked git dir
    if git_dir.is_symlink():
        return False, "git directory is a symlink (security risk)"

    hooks_dir = git_dir / "hooks"
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"failed to create hooks directory: {e}"

    # Security: reject symlinked hooks dir
    if hooks_dir.is_symlink():
        return False, "hooks directory is a symlink (security risk)"

    hook_path = hooks_dir / "pre-push"

    if hook_path.exists() and not force:
        # Security: reject symlinked hook file
        if hook_path.is_symlink():
            return False, "existing pre-push is a symlink (security risk)"
        return False, "pre-push hook already exists (use --force to replace)"

    hook_path.write_text(PRE_PUSH_HOOK, encoding="utf-8")

    # Make executable (no-op on Windows, matters on POSIX)
    try:
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass

    return True, "installed"


def init_project(target_dir, project_name=None, force=False):
    """
    Scaffold aesop orchestration into target_dir.

    Returns a dict: {
        "project_name": str,
        "files_created": [str, ...],
        "files_skipped": [str, ...],
        "hook_status": str,
        "domains_found": [str, ...],
    }
    """
    target = Path(target_dir).resolve()

    if not target.is_dir():
        raise FileNotFoundError(f"Target directory does not exist: {target}")

    # Detect project name
    if not project_name:
        project_name = detect_project_name(str(target))

    # Detect code directories
    domains = discover_code_dirs(str(target))

    # Detect git identity
    identity = detect_git_identity(str(target))

    files_created = []
    files_skipped = []

    # 1. Root CLAUDE.md
    domain_map = build_domain_map(domains)
    root_claude = target / "CLAUDE.md"
    content = ROOT_CLAUDE_MD.format(
        project_name=project_name,
        domain_map=domain_map,
    )
    if write_file(str(root_claude), content, force=force):
        files_created.append("CLAUDE.md")
    else:
        files_skipped.append("CLAUDE.md")

    # 2. Starter domain CLAUDE.md for first discovered directory
    if domains:
        first_domain = domains[0]
        domain_claude = target / first_domain / "CLAUDE.md"
        domain_content = DOMAIN_CLAUDE_MD.format(domain_name=first_domain)
        if write_file(str(domain_claude), domain_content, force=force):
            files_created.append(f"{first_domain}/CLAUDE.md")
        else:
            files_skipped.append(f"{first_domain}/CLAUDE.md")

    # 3. aesop.config.json
    config = dict(DEFAULT_CONFIG)
    config["identity"] = identity
    config_path = target / "aesop.config.json"
    if not config_path.exists() or force:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        files_created.append("aesop.config.json")
    else:
        files_skipped.append("aesop.config.json")

    # 4. state/ directory with .gitkeep
    state_dir = target / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = state_dir / ".gitkeep"
    if write_file(str(gitkeep), "", force=force):
        files_created.append("state/.gitkeep")
    else:
        files_skipped.append("state/.gitkeep")

    # 5. .github/workflows/ci.yml
    ci_path = target / ".github" / "workflows" / "ci.yml"
    if write_file(str(ci_path), CI_YML, force=force):
        files_created.append(".github/workflows/ci.yml")
    else:
        files_skipped.append(".github/workflows/ci.yml")

    # 6. Copy secret_scan.py to tools/
    scan_ok, scan_status = copy_secret_scan_script(str(target))
    if scan_ok:
        files_created.append("tools/secret_scan.py")
    else:
        files_skipped.append(f"tools/secret_scan.py ({scan_status})")

    # 7. Install git pre-push hook
    hook_ok, hook_status = install_pre_push_hook(str(target), force=force)
    if hook_ok:
        files_created.append(".git/hooks/pre-push")

    return {
        "project_name": project_name,
        "files_created": files_created,
        "files_skipped": files_skipped,
        "hook_status": hook_status,
        "domains_found": domains,
    }


def print_summary(result):
    """Print a human-readable Getting Started summary."""
    print(f"\n=== Aesop project initialized: {result['project_name']} ===\n")

    if result["files_created"]:
        print("Created:")
        for f in result["files_created"]:
            print(f"  + {f}")

    if result["files_skipped"]:
        print("\nSkipped (already exist):")
        for f in result["files_skipped"]:
            print(f"  - {f}")

    print(f"\nGit hook: {result['hook_status']}")

    if result["domains_found"]:
        print(f"Detected code directories: {', '.join(result['domains_found'])}")
    else:
        print("No well-known code directories detected (add domains to CLAUDE.md manually).")

    print("\n--- Getting Started ---")
    print("1. Review and customize CLAUDE.md with your project details")
    print("2. Review aesop.config.json (state dir, dashboard port, identity)")
    print("3. Run: npx @matt82198/aesop doctor")
    print("4. Launch dashboard: npx @matt82198/aesop dash")
    print("5. Commit the scaffolded files to your repo")
    print()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scaffold aesop orchestration into a project directory.",
    )
    parser.add_argument(
        "--dir", default=".",
        help="Target directory to scaffold into (default: current directory)",
    )
    parser.add_argument(
        "--name", default=None,
        help="Project name (default: auto-detected from git remote or directory name)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files",
    )

    # Fail-closed on unknown flags
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"Error: unknown arguments: {' '.join(unknown)}", file=sys.stderr)
        parser.print_usage(sys.stderr)
        sys.exit(2)

    target = Path(args.dir).resolve()
    if not target.is_dir():
        print(f"Error: directory does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    try:
        result = init_project(
            str(target),
            project_name=args.name,
            force=args.force,
        )
        print_summary(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
