#!/usr/bin/env python3
"""GitHub Actions self-hosted runner installer for an aesop box (`aesop runner install|remove`).
INDEX: `aesop runner install [--instances N] [--labels ...] [--dry-run] [--set-fork-policy] [--repo OWNER/NAME] [--install-root DIR]` / `aesop runner remove`: preflight = tools/ci_capability.py's probe (REFUSES with the Smart App Control / UMCI message when unsigned runner binaries are blocked, REFUSES on a public repo unless fork-pr-contributor-approval is all_external_contributors -- `--set-fork-policy` sets it via `gh api -X PUT`), then the documented GitHub steps: latest actions/runner release via `gh api`, sha256 from the release notes (`<!-- BEGIN SHA <platform> -->`), registration token via `gh api -X POST repos/<r>/actions/runners/registration-token` used immediately and NEVER printed, `config.cmd|sh --unattended --runasservice --labels ...`, N instances in sibling dirs; `remove` uses the remove-token; `--dry-run` prints the plan (token shown as a placeholder); exit 0 ok / 1 REFUSED or failed / 2 usage; stdlib-only

Nothing is downloaded, registered, or configured until preflight passes and the
plan is built; `--dry-run` stops there. The registration/removal token is fetched
by fetch_token() only inside execute_*() and passed straight to config.cmd/sh.

CLI:
  python tools/runner_install.py install [--repo OWNER/NAME] [--instances N] [--labels L1,L2,...]
                                         [--install-root DIR] [--dry-run] [--set-fork-policy] [--json]
  python tools/runner_install.py remove  [--repo OWNER/NAME] [--instances N] [--install-root DIR] [--dry-run] [--json]
Exit: 0 = ok; 1 = refused or failed; 2 = usage error.
"""
import argparse
import hashlib
import io
import json
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))  # sibling imports below resolve when loaded by file path

import ci_capability  # noqa: E402
from common import CI_DEFAULT_LABELS  # noqa: E402

DEFAULT_LABELS = list(CI_DEFAULT_LABELS)
TOKEN_PLACEHOLDER = "<token: fetched at run time via gh api, used once, never printed>"
REQUIRED_FORK_POLICY = "all_external_contributors"
MAX_INSTANCES = 64

EPILOG = """\
subcommands and flags:
  install   --repo OWNER/NAME        target repository (default: parsed from `git remote get-url origin`)
            --instances N            number of runner instances in sibling dirs (default 1)
            --labels L1,L2,...       runner labels (default self-hosted,windows,aesop-box)
            --install-root DIR       parent dir for runner-<n> dirs (default ~/actions-runner)
            --dry-run                print the plan and stop (nothing downloaded or registered)
            --set-fork-policy        public repo: set fork-pr-contributor-approval=all_external_contributors via gh api
            --json                   print the plan as JSON
  remove    --repo, --instances, --install-root, --dry-run, --json as above; uses the remove-token

preflight refusals (exit 1): Smart App Control / UMCI enforced (unsigned runner binaries blocked),
gh not authenticated, public repo without all_external_contributors approval policy.
"""


class Refusal(Exception):
    """A preflight or integrity check refused to continue; message is user-facing."""


# --- gh plumbing -----------------------------------------------------------

def run_gh(args):
    """Run `gh <args>`; (rc, stdout, stderr). Missing gh -> rc 127."""
    try:
        res = subprocess.run(["gh", *args], capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        return res.returncode, res.stdout or "", res.stderr or ""
    except FileNotFoundError:
        return 127, "", "gh not found on PATH"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def gh_json(gh, args):
    """Run a gh call that must succeed and return JSON."""
    rc, out, err = gh(args)
    if rc != 0:
        raise Refusal("gh %s failed (rc=%s): %s" % (" ".join(args), rc, (err or out).strip()))
    try:
        return json.loads(out) if out.strip() else {}
    except ValueError as exc:
        raise Refusal("gh %s returned non-JSON output: %s" % (" ".join(args), exc))


def detect_repo(cwd=None):
    """OWNER/NAME from the origin remote, or None."""
    try:
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", res.stdout.strip())
    return "%s/%s" % (match.group(1), match.group(2)) if match else None


def fetch_token(repo, kind, gh=run_gh):
    """Fetch a single-use registration-token or remove-token. The ONLY place a token is seen."""
    assert kind in ("registration-token", "remove-token")
    data = gh_json(gh, ["api", "-X", "POST", "repos/%s/actions/runners/%s" % (repo, kind)])
    token = data.get("token")
    if not token:
        raise Refusal("gh api returned no %s for %s" % (kind, repo))
    return token


# --- preflight ---------------------------------------------------------------

def _validate_instances(instances):
    if not isinstance(instances, int) or instances < 1 or instances > MAX_INSTANCES:
        raise Refusal("REFUSED: --instances must be an integer between 1 and %d (got %r)" % (MAX_INSTANCES, instances))


def preflight(repo, probes, gh=run_gh, set_fork_policy=False):
    """Refuse early; return the repo facts the plan records."""
    blocked, reason = ci_capability.runner_blocked(probes)
    if blocked:
        raise Refusal("REFUSED: this machine cannot run the GitHub Actions runner -- %s. "
                      "Use `--ci-mode hosted` (or local-receipt-gate once available), or run the runner on a "
                      "machine where `aesop doctor` reports self-hosted-runner as runnable." % reason)
    gh_info = probes.get("gh") or {}
    if not gh_info.get("present") or not gh_info.get("authenticated"):
        raise Refusal("REFUSED: `gh auth status` is not authenticated; the registration token is minted via "
                      "`gh api`. Run `gh auth login` and retry.")
    if not repo or "/" not in repo:
        raise Refusal("REFUSED: no repository given and none detected from `git remote get-url origin`; pass --repo OWNER/NAME")
    info = gh_json(gh, ["api", "repos/%s" % repo])
    visibility = info.get("visibility") or ("private" if info.get("private") else "public")
    policy = None
    if visibility == "public":
        endpoint = "repos/%s/actions/permissions/fork-pr-contributor-approval" % repo
        policy = (gh_json(gh, ["api", endpoint]) or {}).get("approval_policy")
        if policy != REQUIRED_FORK_POLICY:
            if not set_fork_policy:
                raise Refusal(
                    "REFUSED: %s is public and its fork-pr-contributor-approval policy is %r. A self-hosted runner "
                    "on a public repository executes pull-request code on your hardware, so approval must be required "
                    "for ALL external contributors (%s). Re-run with --set-fork-policy to set it via "
                    "`gh api -X PUT %s -f approval_policy=%s`." % (repo, policy, REQUIRED_FORK_POLICY, endpoint, REQUIRED_FORK_POLICY))
            rc, out, err = gh(["api", "-X", "PUT", endpoint, "-f", "approval_policy=%s" % REQUIRED_FORK_POLICY])
            if rc != 0:
                raise Refusal("REFUSED: could not set fork-pr-contributor-approval on %s: %s" % (repo, (err or out).strip()))
            policy = REQUIRED_FORK_POLICY
    return {"name": repo, "visibility": visibility, "fork_pr_policy": policy}


# --- plan ------------------------------------------------------------------

def platform_key(probes):
    """actions/runner asset key for this box (win-x64, linux-x64, osx-x64, *-arm64)."""
    os_info = probes.get("os") or {}
    system = os_info.get("system") or platform.system()
    machine = (os_info.get("machine") or platform.machine() or "").lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    prefix = {"Windows": "win", "Linux": "linux", "Darwin": "osx"}.get(system)
    if not prefix:
        raise Refusal("REFUSED: unsupported platform %r for the GitHub Actions runner" % system)
    return "%s-%s" % (prefix, arch)


def resolve_release(gh, key):
    """Latest actions/runner release: asset for `key` + sha256 lifted from the release notes."""
    rel = gh_json(gh, ["api", "repos/actions/runner/releases/latest"])
    tag = rel.get("tag_name", "")
    asset = next((a for a in rel.get("assets", []) if ("actions-runner-%s-" % key) in a.get("name", "")), None)
    if not asset:
        raise Refusal("REFUSED: release %s carries no asset for %s" % (tag, key))
    match = re.search(r"<!--\s*BEGIN SHA %s\s*-->\s*([0-9a-fA-F]{64})\s*<!--\s*END SHA %s\s*-->" % (re.escape(key), re.escape(key)),
                      rel.get("body") or "")
    if not match:
        raise Refusal("REFUSED: release notes for %s carry no sha256 for %s; refusing to download an unverifiable archive" % (tag, key))
    return {"tag": tag, "asset": asset["name"], "url": asset.get("browser_download_url", ""), "sha256": match.group(1).lower()}


def verify_sha256(data, expected):
    """Raise Refusal unless data hashes to expected (hex)."""
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != (expected or "").lower():
        raise Refusal("REFUSED: sha256 mismatch for downloaded runner archive (expected %s, got %s)" % (expected, actual))
    return True


def _instances(repo, instances, install_root, probes, hostname=None):
    os_info = probes.get("os") or {}
    is_windows = os_info.get("system") == "Windows"
    host = hostname or os_info.get("hostname") or platform.node() or "aesop-box"
    root = Path(install_root) if install_root else Path.home() / "actions-runner"
    rows = []
    for i in range(1, instances + 1):
        inst_dir = root / ("runner-%d" % i)
        config = str(inst_dir / ("config.cmd" if is_windows else "config.sh"))
        rows.append({"name": "%s-%d" % (host, i), "dir": str(inst_dir), "config": config})
    return rows


def build_install_plan(repo, instances=1, labels=None, probes=None, gh=run_gh, set_fork_policy=False,
                       install_root=None):
    """Preflight + the full install plan. Never fetches a registration token."""
    _validate_instances(instances)
    probes = probes if probes is not None else ci_capability.probe_all()
    repo_info = preflight(repo, probes, gh=gh, set_fork_policy=set_fork_policy)
    labels = list(labels) if labels else list(DEFAULT_LABELS)
    key = platform_key(probes)
    release = resolve_release(gh, key)
    rows = []
    for inst in _instances(repo, instances, install_root, probes):
        cmd = [inst["config"], "--unattended", "--runasservice", "--replace",
               "--url", "https://github.com/%s" % repo, "--token", TOKEN_PLACEHOLDER,
               "--name", inst["name"], "--labels", ",".join(labels), "--work", "_work"]
        rows.append({"name": inst["name"], "dir": inst["dir"], "config_cmd": cmd})
    return {
        "action": "install",
        "repo": repo_info,
        "platform": key,
        "release": release,
        "labels": labels,
        "install_root": str(Path(rows[0]["dir"]).parent),
        "instances": rows,
        "steps": [
            "download %s (%s)" % (release["asset"], release["url"]),
            "verify sha256 == %s (from the release notes)" % release["sha256"],
            "extract into each instance dir",
            "mint a registration token: gh api -X POST repos/%s/actions/runners/registration-token (used immediately, never printed)" % repo,
            "run config with --unattended --runasservice per instance",
        ],
    }


def build_remove_plan(repo, instances=1, gh=run_gh, install_root=None, probes=None):
    """Plan for `aesop runner remove`. Never fetches the remove token."""
    _validate_instances(instances)
    probes = probes if probes is not None else ci_capability.probe_all()
    if not repo or "/" not in repo:
        raise Refusal("REFUSED: no repository given and none detected; pass --repo OWNER/NAME")
    rows = []
    for inst in _instances(repo, instances, install_root, probes):
        rows.append({"name": inst["name"], "dir": inst["dir"], "config_cmd": [inst["config"], "remove", "--token", TOKEN_PLACEHOLDER]})
    return {
        "action": "remove", "repo": {"name": repo}, "instances": rows,
        "install_root": str(Path(rows[0]["dir"]).parent),
        "steps": ["mint a remove token: gh api -X POST repos/%s/actions/runners/remove-token (used immediately, never printed)" % repo,
                  "run `config remove` per instance, then delete the instance dir"],
    }


def render_plan(plan):
    """Human-readable plan. Tokens appear only as the placeholder."""
    lines = ["runner %s plan for %s" % (plan["action"], plan["repo"]["name"])]
    if plan["action"] == "install":
        lines.append("  repo visibility: %s; fork PR policy: %s" % (plan["repo"]["visibility"], plan["repo"]["fork_pr_policy"]))
        lines.append("  release: %s asset %s" % (plan["release"]["tag"], plan["release"]["asset"]))
        lines.append("  sha256: %s" % plan["release"]["sha256"])
        lines.append("  labels: %s" % ",".join(plan["labels"]))
    lines.append("  install root: %s" % plan["install_root"])
    for inst in plan["instances"]:
        lines.append("  instance %s -> %s" % (inst["name"], inst["dir"]))
        lines.append("    %s" % " ".join(inst["config_cmd"]))
    lines.append("  steps:")
    for step in plan["steps"]:
        lines.append("    - %s" % step)
    return "\n".join(lines)


# --- execution (never reached by tests or --dry-run) ------------------------------

def _download(url):
    with urllib.request.urlopen(url, timeout=300) as resp:  # nosec - GitHub release asset
        return resp.read()


def _extract(archive_name, data, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(dest)


def _run_config(cmd, token, cwd):
    real = [token if part == TOKEN_PLACEHOLDER else part for part in cmd]
    res = subprocess.run(real, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    shown = " ".join(cmd)
    if res.returncode != 0:
        raise Refusal("config failed (rc=%s): %s\n%s" % (res.returncode, shown, (res.stderr or res.stdout).strip()))
    return shown


def execute_install(plan, gh=run_gh, download=_download):
    """Download, verify, extract, register N instances. Prints the placeholder, never the token."""
    data = download(plan["release"]["url"])
    verify_sha256(data, plan["release"]["sha256"])
    for inst in plan["instances"]:
        _extract(plan["release"]["asset"], data, Path(inst["dir"]))
    for inst in plan["instances"]:
        token = fetch_token(plan["repo"]["name"], "registration-token", gh=gh)
        print("configured: %s" % _run_config(inst["config_cmd"], token, inst["dir"]))
    return 0


def execute_remove(plan, gh=run_gh):
    """Unregister N instances with a remove token each."""
    for inst in plan["instances"]:
        if not Path(inst["config"] if "config" in inst else inst["config_cmd"][0]).exists():
            print("skip: %s has no runner config at %s" % (inst["name"], inst["dir"]))
            continue
        token = fetch_token(plan["repo"]["name"], "remove-token", gh=gh)
        print("removed: %s" % _run_config(inst["config_cmd"], token, inst["dir"]))
    return 0


# --- CLI -----------------------------------------------------------------

def _parse_labels(value):
    if not value:
        return None
    labels = [x.strip() for part in value for x in part.split(",")] if isinstance(value, list) else \
        [x.strip() for x in value.split(",")]
    return [x for x in labels if x]


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Install or remove GitHub Actions self-hosted runner instances on this aesop box.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    for name in ("install", "remove"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=None, help="OWNER/NAME (default: origin remote)")
        p.add_argument("--instances", type=int, default=1, help="number of runner instances (default 1)")
        p.add_argument("--install-root", default=None, help="parent directory for runner-<n> dirs")
        p.add_argument("--dry-run", action="store_true", help="print the plan and stop")
        p.add_argument("--json", action="store_true", help="print the plan as JSON")
        if name == "install":
            p.add_argument("--labels", nargs="+", default=None, help="runner labels, comma- or space-separated")
            p.add_argument("--set-fork-policy", action="store_true",
                           help="public repo: set fork-pr-contributor-approval to all_external_contributors")
    args = parser.parse_args(argv)

    repo = args.repo or detect_repo()
    try:
        if args.command == "install":
            plan = build_install_plan(repo, instances=args.instances, labels=_parse_labels(args.labels),
                                      set_fork_policy=args.set_fork_policy, install_root=args.install_root)
        else:
            plan = build_remove_plan(repo, instances=args.instances, install_root=args.install_root)
        if args.json:
            print(json.dumps(plan, indent=2, sort_keys=True))
        else:
            print(render_plan(plan))
        if args.dry_run:
            print("dry-run: nothing downloaded, registered, or removed")
            return 0
        return execute_install(plan) if args.command == "install" else execute_remove(plan)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
