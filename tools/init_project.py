#!/usr/bin/env python3
"""
Project initialization scaffolder for aesop.
INDEX: Project scaffolder (`aesop init [--ci-mode hosted|self-hosted-runner|local-receipt-gate] [--self-hosted-labels L1,L2]`): creates CLAUDE.md, config (with the `ci` block for the chosen mode), state dir, the CI workflow(s) rendered from templates/ci/ (ci-hosted.yml | ci-self-hosted-windows.yml with the fork-routing runs-on + labels | verify-receipt.yml copied from the receipt lane's real workflow when present, else REFUSED with a clear message because the template is a documented placeholder), pre-push hook, and copies secret_scan.py; validates the mode and renders every workflow BEFORE writing anything

When a user runs `npx @matt82198/aesop init` in a new repo, this tool
scaffolds the aesop orchestration layer:

1. Root CLAUDE.md with project purpose placeholder, domain map, dispatch rule
2. A starter domain CLAUDE.md for the first discovered code directory
3. aesop.config.json with sensible defaults
4. state/ directory with .gitkeep
5. .github/workflows/ci.yml rendered from templates/ci/ for the chosen --ci-mode
   (plus .github/workflows/verify-receipt.yml for local-receipt-gate)
6. tools/secret_scan.py (copied from aesop repo, fail-closed gate)
7. Git pre-push hook (secret scan gate with fail-closed logic)
8. Prints a "Getting Started" summary

CLI: python tools/init_project.py [--dir PATH] [--name PROJECT_NAME] [--force]
                                  [--ci-mode hosted|self-hosted-runner|local-receipt-gate]
                                  [--self-hosted-labels L1,L2,...]
Exit: 0=success, 1=error or scaffold refused, 2=usage error
"""
import argparse
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))  # sibling imports below resolve when loaded by file path

from common import CI_MODES, CI_DEFAULT_LABELS, validate_ci_config  # noqa: E402

# --- CI mode templates ------------------------------------------------------

TEMPLATES_CI_DIR = TOOLS_DIR.parent / "templates" / "ci"
VERIFY_RECEIPT_REL = Path(".github") / "workflows" / "verify-receipt.yml"
LABELS_MARKER = "__AESOP_SELF_HOSTED_LABELS_JSON__"
PLACEHOLDER_MARKER = "aesop-template: PLACEHOLDER"
RECEIPT_LANE = "feat/receipt-gate-increment-1"
CI_MODE_TEMPLATES = {
    "hosted": "ci-hosted.yml",
    "self-hosted-runner": "ci-self-hosted-windows.yml",
    "local-receipt-gate": "ci-hosted.yml",
}


class ScaffoldRefused(Exception):
    """The scaffold refused to emit a file; the message says why and what to do."""


def is_placeholder_template(text):
    """True when a template is the documented stand-in that must never be scaffolded."""
    return PLACEHOLDER_MARKER in (text or "")


def _read_template(name):
    path = TEMPLATES_CI_DIR / name
    if not path.is_file():
        raise ScaffoldRefused("CI template missing from the aesop package: %s" % path)
    return path.read_text(encoding="utf-8")


def ci_config_block(ci_mode, labels=None):
    """The `ci` block of aesop.config.json for a scaffolded mode."""
    labels = list(labels) if labels else list(CI_DEFAULT_LABELS)
    if ci_mode == "hosted":
        block = {"mode": ["hosted"], "windowsMatrix": "hosted", "receiptGate": "off"}
    elif ci_mode == "self-hosted-runner":
        block = {"mode": ["self-hosted-runner"], "windowsMatrix": "self-hosted", "receiptGate": "off"}
    elif ci_mode == "local-receipt-gate":
        block = {"mode": ["hosted", "local-receipt-gate"], "windowsMatrix": "hosted", "receiptGate": "alongside"}
    else:
        raise ValueError("unknown --ci-mode %r (choose from %s)" % (ci_mode, ", ".join(CI_MODES)))
    block["selfHostedLabels"] = labels
    return block


def render_ci_workflows(ci_mode, labels=None, aesop_root=None):
    """Return {relative path: content} for every workflow the mode needs.

    Raises ValueError on an unknown mode and ScaffoldRefused when a required
    workflow would have to come from the placeholder template.
    """
    if ci_mode not in CI_MODE_TEMPLATES:
        raise ValueError("unknown --ci-mode %r (choose from %s)" % (ci_mode, ", ".join(CI_MODES)))
    labels = list(labels) if labels else list(CI_DEFAULT_LABELS)
    text = _read_template(CI_MODE_TEMPLATES[ci_mode])
    if is_placeholder_template(text):
        raise ScaffoldRefused("CI template %s is a placeholder; refusing to scaffold it" % CI_MODE_TEMPLATES[ci_mode])
    if ci_mode == "self-hosted-runner":
        text = text.replace(LABELS_MARKER, json.dumps(labels, separators=(",", ":")))
    files = {".github/workflows/ci.yml": text}
    if ci_mode == "local-receipt-gate":
        root = Path(aesop_root).resolve() if aesop_root else TOOLS_DIR.parent
        real = root / VERIFY_RECEIPT_REL
        if real.is_file() and not is_placeholder_template(real.read_text(encoding="utf-8")):
            files[".github/workflows/verify-receipt.yml"] = real.read_text(encoding="utf-8")
        else:
            template = _read_template("verify-receipt.yml")
            if is_placeholder_template(template):
                raise ScaffoldRefused(
                    "REFUSED: --ci-mode local-receipt-gate needs .github/workflows/verify-receipt.yml, and this "
                    "aesop package only carries the documented placeholder (templates/ci/verify-receipt.yml). "
                    "The receipt gate ships on branch %s (tools/emit_receipt.py, tools/verify_receipt.py, "
                    "verify-receipt.yml); until it lands, scaffold with --ci-mode hosted or self-hosted-runner. "
                    "Nothing was written." % RECEIPT_LANE)
            files[".github/workflows/verify-receipt.yml"] = template
    return files


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
    # tools/doctor.js checkConfig requires the repos key (an array) to be present.
    "repos": [],
    "cardinal_rules": {
        "subagent_model": "haiku",
        "tdd_first": True,
        "never_push_main": True,
        "secret_scan_gates_push": True
    }
}

# Push-gate artifacts the pre-push hook and `aesop doctor` require. Same generators and
# arguments as bin/cli.js (PUSH_GATE_ARTIFACTS) and tools/doctor.js; run here from the
# aesop tools dir with cwd set to the target repo, because init does not copy tools/.
PUSH_GATE_GENERATORS = [
    ("tools/INDEX.md", "gen_tool_index.py", ["--regenerate"]),
    (".pyflakes-baseline.json", "pyflakes_gate.py",
     ["--root", ".", "--baseline", ".pyflakes-baseline.json", "--update-baseline"]),
    (".subprocess-guard-baseline.json", "subprocess_guard.py",
     ["--baseline", ".subprocess-guard-baseline.json", "--update-baseline"]),
    (".portability-baseline.json", "portability_check.py",
     ["--root", ".", "--baseline", ".portability-baseline.json", "--update-baseline"]),
    (".stateapi-baseline.json", "stateapi_lint.py",
     ["--root", ".", "--baseline", ".stateapi-baseline.json", "--update-baseline"]),
]


def _run_push_gate_generator(target, script, args):
    """Run one generator with cwd=target. Returns None on success, else an error string."""
    try:
        proc = subprocess.run(
            [sys.executable, str(TOOLS_DIR / script), *args],
            cwd=str(target), capture_output=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    return None


def generate_push_gate_artifacts(target, force=False):
    """Generate the push-gate artifacts in target; return (created, skipped) file lists.

    An existing artifact is kept unless force is set (same rule as write_file). Failures never
    abort the scaffold: each prints a warning naming the file and its regenerate command, and
    the pre-push hook and `aesop doctor` fail closed until the file exists.
    AESOP_SKIP_PUSH_GATE_ARTIFACTS=1 skips generation (matches bin/cli.js).
    """
    target = Path(target)
    if os.environ.get("AESOP_SKIP_PUSH_GATE_ARTIFACTS") == "1":
        return [], []
    skipped = [rel for rel, _, _ in PUSH_GATE_GENERATORS if (target / rel).exists() and not force]
    todo = [g for g in PUSH_GATE_GENERATORS if g[0] not in skipped]
    if not todo:
        return [], skipped
    if not (target / ".git").exists():
        print("WARN: no git repository: push-gate artifacts were not generated "
              "(the pre-push hook will block until they are).", file=sys.stderr)
        return [], skipped
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        futures = [(rel, script, args, pool.submit(_run_push_gate_generator, target, script, args))
                   for rel, script, args in todo]
        results = [(rel, script, args, fut.result()) for rel, script, args, fut in futures]
    created = []
    for rel, script, args, err in results:
        if err is None and (target / rel).is_file():
            created.append(rel)
        else:
            cmd = " ".join(["python", f"tools/{script}", *args])
            print(f"WARN: could not generate {rel}: {err or 'file missing'}\n"
                  f"  regenerate with: cd {target} && {cmd}", file=sys.stderr)
    return created, skipped


PRE_PUSH_HOOK = """\
#!/usr/bin/env bash
set -uo pipefail

# Aesop pre-push hook: branch protection + secret scan gate
# Installed by tools/init_project.py
# git feeds one stdin line per pushed ref: <local_ref> <local_sha> <remote_ref> <remote_sha>

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
  while read -r local_ref local_sha remote_ref remote_sha; do
    # Fail closed on a malformed line (fewer than four fields): never fall back to a whole-tree scan
    if [ -z "$local_ref" ] || [ -z "$local_sha" ] || [ -z "$remote_ref" ] || [ -z "$remote_sha" ]; then
      echo "ERROR: Malformed pre-push stdin line (expected: <local_ref> <local_sha> <remote_ref> <remote_sha>). Push blocked."
      exit 1
    fi
    # Protected remote refs are blocked whatever branch is checked out
    case "$remote_ref" in
      refs/heads/main|refs/heads/master)
        echo "ERROR: Push to $remote_ref is blocked by policy (protected remote ref)."
        echo "Use a feature branch and open a pull request."
        exit 1
        ;;
    esac
    # Deleting a ref pushes no content
    case "$local_sha" in *[!0]*) ;; *) continue ;; esac
    # Scan the commits being pushed: remote tip..local tip, or the whole tree for a new ref
    base=""
    case "$remote_sha" in *[!0]*) base="$remote_sha" ;; esac
    if [ -z "$base" ] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then
      base=$(git hash-object -t tree /dev/null)
    fi
    python3 "$scan_script" --range "$base..$local_sha" --repo "$repo_root" < /dev/null
    scan_exit=$?
    if [ $scan_exit -ne 0 ]; then
      echo "ERROR: Secret scan found issues in $local_ref. Push blocked."
      exit 1
    fi
  done
else
  echo "ERROR: Secret scan script not found at $scan_script"
  echo "The pre-push hook cannot run. Push blocked."
  echo "This is likely a setup error: tools/secret_scan.py must be present in the repo."
  exit 1
fi

# Pushed-range secret scan: --staged only sees the index, so committed secrets
# would pass. git feeds one line per ref on stdin:
#   <local_ref> <local_sha> <remote_ref> <remote_sha>
# Every non-deletion line scans the pushed commits with --range (fail closed).
zero_sha=0000000000000000000000000000000000000000
empty_tree_sha=4b825dc642cb6eb9a060e54bf8d69288fbee4904
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -z "${local_ref:-}" ] && continue
  if [ -z "${remote_sha:-}" ] || [ -z "${local_sha:-}" ]; then
    echo "ERROR: Malformed pre-push input line for $local_ref. Push blocked."
    exit 1
  fi
  # Branch deletion pushes no commits.
  [ "$local_sha" = "$zero_sha" ] && continue
  if [ "$remote_sha" = "$zero_sha" ]; then
    # New branch: scan from the merge-base with the default branch, else the whole history.
    base_sha=""
    for default_ref in origin/HEAD origin/main origin/master main master; do
      if git rev-parse --verify -q "$default_ref^{commit}" >/dev/null 2>&1; then
        base_sha=$(git merge-base "$local_sha" "$default_ref" 2>/dev/null || echo "")
        break
      fi
    done
    [ -z "$base_sha" ] && base_sha="$empty_tree_sha"
    pushed_range="$base_sha..$local_sha"
  else
    pushed_range="$remote_sha..$local_sha"
  fi
  python3 "$scan_script" --range "$pushed_range" --repo "$repo_root" </dev/null
  range_exit=$?
  if [ $range_exit -ne 0 ]; then
    echo "ERROR: Secret scan found issues in pushed commits ($pushed_range). Push blocked."
    exit 1
  fi
done

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
                errors="replace",
                cwd=str(target),
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = result.stdout.strip()
                # Resolve relative paths
                if Path(common_dir).is_absolute():
                    return Path(common_dir).resolve()
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
                        return p.resolve()
        except (OSError, UnicodeDecodeError):
            pass

        return None
    elif git_path.is_dir():
        # Regular git directory
        return git_path
    else:
        # .git exists but is neither file nor directory (symlink/other)
        return None


def copy_secret_scan_script(target_dir, force=False):
    """
    Copy tools/secret_scan.py from the aesop repo into the target repo's tools/ dir.
    Skips if already present unless force=True (init --force refreshes a stale copy).

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

        # Skip if already exists (unless force)
        if target_scan_path.exists() and not force:
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

    # Security: reject a symlinked hook file even under force. write_bytes follows the
    # link, so --force would overwrite the link's target outside .git/hooks.
    if hook_path.is_symlink():
        return False, "existing pre-push is a symlink (security risk)"

    if hook_path.exists() and not force:
        return False, "pre-push hook already exists (use --force to replace)"

    # Write bytes so the POSIX hook keeps LF endings on Windows (write_text would emit CRLF).
    hook_path.write_bytes(PRE_PUSH_HOOK.encode("utf-8"))

    # Make executable (no-op on Windows, matters on POSIX)
    try:
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass

    return True, "installed"


STATE_IGNORE_BEGIN = "# >>> aesop:state-ignore-begin (runtime state; do not commit) >>>"
STATE_IGNORE_END = "# <<< aesop:state-ignore-end <<<"
STATE_IGNORE_BLOCK = (
    f"{STATE_IGNORE_BEGIN}\n"
    "state/*\n"
    "!state/.gitkeep\n"
    f"{STATE_IGNORE_END}\n"
)


def ensure_state_gitignore(target_dir):
    """Append a marker-delimited block ignoring state/ runtime files.

    Idempotent: returns False and leaves the file untouched when the block is
    already present. Creates .gitignore when absent, appends otherwise.
    """
    gitignore = Path(target_dir) / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if STATE_IGNORE_BEGIN in existing:
        return False
    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    gitignore.write_text(existing + sep + STATE_IGNORE_BLOCK, encoding="utf-8")
    return True


def init_project(target_dir, project_name=None, force=False, ci_mode="hosted",
                 self_hosted_labels=None, aesop_root=None):
    """
    Scaffold aesop orchestration into target_dir.

    ci_mode selects the workflow template(s) and the `ci` block written to
    aesop.config.json. The mode is validated and every workflow is rendered
    BEFORE any file is written, so a refused or unknown mode leaves the
    directory untouched (ValueError / ScaffoldRefused).

    Returns a dict: {
        "project_name": str,
        "files_created": [str, ...],
        "files_skipped": [str, ...],
        "hook_status": str,
        "domains_found": [str, ...],
        "ci_mode": str,
    }
    """
    target = Path(target_dir).resolve()

    if not target.is_dir():
        raise FileNotFoundError(f"Target directory does not exist: {target}")

    # Validate + render first: nothing below may run for a bad or refused mode.
    workflows = render_ci_workflows(ci_mode, labels=self_hosted_labels, aesop_root=aesop_root)
    ci_block = ci_config_block(ci_mode, labels=self_hosted_labels)

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
    config["ci"] = ci_block
    config["layout"] = "init"  # tools/doctor.js: an init'd repo has no scaffold directories
    ci_findings = validate_ci_config(config, str(target))
    if ci_findings:
        raise ScaffoldRefused("scaffolded ci block is invalid: " + "; ".join(f["message"] for f in ci_findings))
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
    # Runtime state (SQLite event store, tracker json) must never be committed.
    if ensure_state_gitignore(str(target)):
        files_created.append(".gitignore (state/ block)")
    else:
        files_skipped.append(".gitignore (state/ block already present)")

    # 5. CI workflow(s) for the chosen mode (rendered above, before any write)
    for rel, content in workflows.items():
        if write_file(str(target / Path(rel)), content, force=force):
            files_created.append(rel)
        else:
            files_skipped.append(rel)

    # 6. Copy secret_scan.py to tools/
    scan_ok, scan_status = copy_secret_scan_script(str(target), force=force)
    if scan_ok:
        files_created.append("tools/secret_scan.py")
    else:
        files_skipped.append(f"tools/secret_scan.py ({scan_status})")

    # 7. Install git pre-push hook
    hook_ok, hook_status = install_pre_push_hook(str(target), force=force)
    if hook_ok:
        files_created.append(".git/hooks/pre-push")

    # 8. Push-gate artifacts doctor and the pre-push hook require (needs the git repo)
    gate_created, gate_skipped = generate_push_gate_artifacts(target, force=force)
    files_created.extend(gate_created)
    files_skipped.extend(gate_skipped)

    return {
        "project_name": project_name,
        "files_created": files_created,
        "files_skipped": files_skipped,
        "hook_status": hook_status,
        "domains_found": domains,
        "ci_mode": ci_mode,
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
    print(f"CI mode: {result['ci_mode']} (run `npx @matt82198/aesop doctor` for the mode -> runnable table)")

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
    parser.add_argument(
        "--ci-mode", default="hosted", choices=list(CI_MODES),
        help="CI mode: which workflow template(s) to write and the ci block of aesop.config.json (default: hosted)",
    )
    parser.add_argument(
        "--self-hosted-labels", default=None,
        help="Comma-separated runner labels rendered into the self-hosted workflow (default: %s)" % ",".join(CI_DEFAULT_LABELS),
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

    labels = None
    if args.self_hosted_labels:
        labels = [x.strip() for x in args.self_hosted_labels.split(",") if x.strip()]

    try:
        result = init_project(
            str(target),
            project_name=args.name,
            force=args.force,
            ci_mode=args.ci_mode,
            self_hosted_labels=labels,
        )
        print_summary(result)
    except ScaffoldRefused as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
