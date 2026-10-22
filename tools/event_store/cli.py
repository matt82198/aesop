"""event_store CLI. Run as: python -I tools/event_store/cli.py append|verify|project ...

Exit codes: 0 ok, 1 failure (verify problems, git commit failure, unreadable or broken
store in a projection), 2 refused/usage.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_store import project as proj_mod  # noqa: E402
from event_store import registry as reg_mod  # noqa: E402
from event_store.store import Store, StoreError  # noqa: E402

REPO_ENCODED_KEY = re.compile(r"^[A-Za-z0-9]+_(pr|issue|pull)_\d+$")


def refuse(msg):
    sys.stderr.write("refused: %s\n" % msg)
    return 2


def parse_refs(items):
    refs = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--ref needs key=value, got %r" % item)
        k, v = item.split("=", 1)
        if REPO_ENCODED_KEY.match(k):
            raise ValueError("ref key %r encodes a repo; use --project <repo> plus "
                             "--ref pr=<n> instead" % k)
        refs[k] = v
    return refs


def git_commit(store_path, event):
    """Commit the store worktree if it is its own git repo. Never pushes."""
    top = subprocess.run(
        ["git", "-C", str(store_path), "rev-parse", "--show-toplevel"],
        capture_output=True, encoding="utf-8", errors="replace")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(store_path).resolve():
        return True
    msg = "event %d %s\n" % (event["seq"], event["kind"])
    fd, name = tempfile.mkstemp(prefix="evmsg-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(msg)
        for cmd in (["add", "-A", "--", "."], ["commit", "-q", "-F", name]):
            r = subprocess.run(["git", "-C", str(store_path), *cmd],
                               capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                sys.stderr.write("git %s failed: %s\n" % (cmd[0], r.stderr.strip()))
                return False
    finally:
        os.unlink(name)
    return True


def cmd_append(args):
    try:
        refs = parse_refs(args.ref)
        reg = reg_mod.load_registry()
    except (ValueError, reg_mod.RegistryError) as exc:
        return refuse(str(exc))
    project = args.project
    if not project:
        goal = refs.get("goal")
        if not goal:
            return refuse("no --project and no --ref goal=G<n>; a new event never "
                          "defaults to aesop")
        project = reg_mod.project_for_goal(reg, goal)
        if not project:
            return refuse("goal %r maps to no project in the registry" % goal)
    if args.kind == "ruling" and not refs.get("goal"):
        return refuse("a ruling needs --ref goal=G<n>")
    try:
        path = reg_mod.store_path(reg, project)
        store = Store(path)
    except (reg_mod.RegistryError, StoreError) as exc:
        return refuse(str(exc))
    if store.project != project:
        return refuse("store at %s belongs to %r, not %r (single-writer check)"
                      % (path, store.project, project))
    try:
        event = store.append(args.kind, args.actor, args.text,
                             session=args.session, refs=refs)
    except StoreError as exc:
        return refuse(str(exc))
    if not git_commit(path, event):
        return 1
    sys.stdout.write("appended %s seq=%d\n" % (project, event["seq"]))
    return 0


def cmd_verify(args):
    try:
        if args.path:
            paths = [Path(args.path)]
        elif args.project:
            reg = reg_mod.load_registry()
            paths = [reg_mod.store_path(reg, args.project)]
        else:
            reg = reg_mod.load_registry()
            paths = [reg_mod.store_path(reg, p) for p in reg["stores"]]
        bad = 0
        for p in paths:
            problems = Store(p).verify()
            for line in problems:
                sys.stdout.write("%s: %s\n" % (p, line))
            bad += len(problems)
    except (reg_mod.RegistryError, StoreError) as exc:
        return refuse(str(exc))
    sys.stdout.write("verify: %d problem(s)\n" % bad)
    return 1 if bad else 0


def cmd_project(args):
    """Read-only projections: pending | rulings | checkpoints over one or all stores."""
    if bool(args.project) == bool(args.all_stores):
        return refuse("project needs exactly one of --project or --all-stores")
    if args.by_goal and args.view != "rulings":
        return refuse("--by-goal applies to rulings only")
    if args.view == "checkpoints":
        if not args.per_goal and args.last <= 0:
            return refuse("checkpoints needs --last N (N > 0) or --per-goal")
        if args.per_store and args.per_goal:
            return refuse("--per-store and --per-goal are exclusive")
    elif args.per_store or args.per_goal or args.last:
        return refuse("--last, --per-store and --per-goal apply to checkpoints only")
    try:
        reg = reg_mod.load_registry()
    except reg_mod.RegistryError as exc:
        return refuse(str(exc))
    projects = [args.project] if args.project else sorted(reg["stores"])
    code, lines = proj_mod.render(args.view, reg, projects, last=args.last,
                                  per_store=args.per_store, per_goal=args.per_goal,
                                  by_goal=args.by_goal)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in lines:
        sys.stdout.write(line + "\n")
    return code


def build_parser():
    ap = argparse.ArgumentParser(prog="event_store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--project")
    a.add_argument("--kind", required=True)
    a.add_argument("--actor", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--session", default=os.environ.get("CLAUDE_SESSION_ID", ""))
    a.add_argument("--ref", action="append", default=[])
    a.set_defaults(fn=cmd_append)
    v = sub.add_parser("verify")
    v.add_argument("--project")
    v.add_argument("--path")
    v.set_defaults(fn=cmd_verify)
    p = sub.add_parser("project")
    p.add_argument("view", choices=("pending", "rulings", "checkpoints"))
    p.add_argument("--project")
    p.add_argument("--all-stores", action="store_true")
    p.add_argument("--by-goal", action="store_true")
    p.add_argument("--last", type=int, default=0)
    p.add_argument("--per-store", action="store_true")
    p.add_argument("--per-goal", action="store_true")
    p.set_defaults(fn=cmd_project)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
