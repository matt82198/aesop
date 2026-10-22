"""One-time migration: split the frozen v1 events/log into per-project v2 stores.

Usage:
    python -I tools/event_store/migrate.py --source <v1 store dir> \
        --targets aesop=<dir> keyrun=<dir> psinasty=<dir> brain=<dir> [--dry-run]

The source is a v1 store: <source>/events/*.jsonl day files (or *.jsonl directly in
<source>), each line {ts, seq, kind, actor, session, text, refs, prev} with a per-day
prev chain (sha256 of the previous line's bytes, "" for the first). The migration
runs only when the LAST source line is a `note` carrying refs.frozen=1 (the FROZEN
note). src_seq is the 1-based ordinal across all day files in sorted file order (for a
single day file it equals the v1 seq).

Attribution (plan docs/plans/PER-PROJECT-EVENT-STORES.md section 4.2, first match
wins, mechanical). Stable rule ids:
    goal     refs.goal: G0 and G1 -> aesop, G2 -> keyrun, G3 -> psinasty
    repo     refs.repo: keyrun -> keyrun, aesop -> aesop,
             claude-config / claude-scripts / conductor3 -> brain
    refkey   ref keys naming exactly one repo (keyrun*, cc* or cs*, aesop*)
    text1    text naming exactly one project
    multi    text naming two or more projects: whole event to aesop, refs.mentions
    default  everything else -> aesop

Each target gets <dir>/events/0000-genesis.jsonl: migrated_genesis, that project's
events in source order, migrated_close. The manifest is written to
<source>/migration-manifest.json on a real run only.

Exit 0 on success; exit 1 (writing nothing) on any refusal.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PROJECTS = ("aesop", "keyrun", "psinasty", "brain")
RULES = ("goal", "repo", "refkey", "text1", "multi", "default")
GENESIS_FILE = "0000-genesis.jsonl"
V1_KEYS = frozenset({"ts", "seq", "kind", "actor", "session", "text", "refs", "prev"})
SRC_LABEL = "conductor3/events/log"

GOAL_RE = re.compile(r"^G(\d+)(?:\.\d+)*$", re.IGNORECASE)
GOAL_PROJECT = {0: "aesop", 1: "aesop", 2: "keyrun", 3: "psinasty"}
REPO_PROJECT = {
    "keyrun": "keyrun",
    "aesop": "aesop",
    "claude-config": "brain",
    "claude-scripts": "brain",
    "conductor3": "brain",
}
# A ref key names a repo when it is the prefix alone or the prefix followed by a
# separator or digit (keyrun_pr_606, cc12, aesop-pr-3); "cc" must not match "ccx".
REFKEY_RE = re.compile(r"^(keyrun|cc|cs|aesop)(?:$|[_\-.0-9])", re.IGNORECASE)
REFKEY_PROJECT = {"keyrun": "keyrun", "cc": "brain", "cs": "brain", "aesop": "aesop"}
TEXT_PATTERNS = (
    ("aesop", re.compile(r"\baesop\b", re.IGNORECASE)),
    ("keyrun", re.compile(r"\bkeyrun\b", re.IGNORECASE)),
    ("psinasty", re.compile(r"psinasty|dynasty|sleeper", re.IGNORECASE)),
    ("brain", re.compile(r"claude-config|claude-scripts|conductor3|\.claude/|\bbrain\b",
                         re.IGNORECASE)),
)


class MigrateError(Exception):
    """A refusal; the CLI prints it and exits 1 having written nothing."""


def line_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# ----------------------------------------------------------------- attribution

def attribute(event):
    """Return (project, rule, mentions). mentions is a list only for rule multi."""
    refs = event.get("refs") or {}
    goal = refs.get("goal")
    if isinstance(goal, str):
        match = GOAL_RE.match(goal.strip())
        if match and int(match.group(1)) in GOAL_PROJECT:
            return GOAL_PROJECT[int(match.group(1))], "goal", None
    repo = refs.get("repo")
    if isinstance(repo, str) and repo.strip().lower() in REPO_PROJECT:
        return REPO_PROJECT[repo.strip().lower()], "repo", None
    named = {REFKEY_PROJECT[m.group(1).lower()]
             for m in (REFKEY_RE.match(str(k)) for k in refs) if m}
    if len(named) == 1:
        return named.pop(), "refkey", None
    text = event.get("text") or ""
    hits = [name for name, pat in TEXT_PATTERNS if pat.search(text)]
    if len(hits) == 1:
        return hits[0], "text1", None
    if len(hits) >= 2:
        return "aesop", "multi", hits
    return "aesop", "default", None


# ---------------------------------------------------------------------- source

def day_files(source):
    source = Path(source)
    files = sorted((source / "events").glob("*.jsonl")) if (source / "events").is_dir() else []
    return files or sorted(source.glob("*.jsonl"))


def read_source(source):
    """Return [(raw_bytes, event)] in source order after verifying every day chain."""
    files = day_files(source)
    if not files:
        raise MigrateError(f"refused: no v1 day files under {source}")
    rows = []
    for path in files:
        data = path.read_bytes()
        prev = ""
        for index, raw in enumerate([x.rstrip(b"\r") for x in data.split(b"\n")][:-1]
                                    if data.endswith(b"\n") else
                                    [x.rstrip(b"\r") for x in data.split(b"\n")], start=1):
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise MigrateError(f"refused: CHAIN BROKEN {path.name}:{index}")
            if (not isinstance(obj, dict) or set(obj) != V1_KEYS or obj["seq"] != index
                    or obj["prev"] != prev):
                raise MigrateError(f"refused: CHAIN BROKEN {path.name}:{index}")
            prev = line_sha(raw)
            rows.append((raw, obj))
    if not rows:
        raise MigrateError("refused: source has no events")
    return rows


def require_frozen(rows):
    last = rows[-1][1]
    frozen = (last.get("refs") or {}).get("frozen")
    if last["kind"] != "note" or str(frozen) != "1":
        raise MigrateError("refused: source is not frozen (last line must be a note "
                           "with refs.frozen=1)")


# ----------------------------------------------------------------------- plan

def build_plan(rows):
    """Attribute every row. Returns (manifest_rows, per-project event lists)."""
    manifest = []
    per_project = {p: [] for p in PROJECTS}
    for src_seq, (raw, event) in enumerate(rows, start=1):
        project, rule, mentions = attribute(event)
        sha = line_sha(raw)
        manifest.append({"src_seq": src_seq, "src_sha": sha, "project": project,
                         "rule": rule})
        refs = dict(event["refs"])
        refs.update({"src_seq": src_seq, "src_sha": sha, "split_rule": rule})
        if mentions is not None:
            refs["mentions"] = mentions
        per_project[project].append({
            "actor": event["actor"], "kind": event["kind"], "refs": refs,
            "session": event["session"], "text": event["text"], "ts": event["ts"],
        })
    return manifest, per_project


def counts_of(manifest):
    by_project = {p: 0 for p in PROJECTS}
    by_rule = {r: 0 for r in RULES}
    for row in manifest:
        by_project[row["project"]] += 1
        by_rule[row["rule"]] += 1
    return by_project, by_rule


def manifest_sha_of(manifest):
    return line_sha(b"\n".join(dump(row) for row in manifest))


# ---------------------------------------------------------------------- writer
# NOTE: this v2 line writer is self-contained on purpose; lane L1's store.py Store
# class replaces it in wave B.

def render_store(events, genesis_refs, close_ts):
    """Return the bytes of one store's genesis file (chained v2 lines)."""
    first_ts = events[0]["ts"] if events else close_ts
    entries = [{"actor": "migrate", "kind": "migrated_genesis", "refs": genesis_refs,
                "session": "migrate", "text": "migrated from " + SRC_LABEL, "ts": first_ts}]
    entries += events
    entries.append({"actor": "migrate", "kind": "migrated_close",
                    "refs": {"count": len(events)}, "session": "migrate",
                    "text": "migration complete", "ts": close_ts})
    out = []
    prev = ""
    for seq, entry in enumerate(entries, start=1):
        line = dict(entry)
        line.update({"v": 2, "seq": seq, "prev": prev})
        raw = dump(line)
        out.append(raw)
        prev = line_sha(raw)
    return b"\n".join(out) + b"\n"


def target_file(target):
    return Path(target) / "events" / GENESIS_FILE


def require_empty_targets(targets):
    for name, target in targets.items():
        events = Path(target) / "events"
        if events.is_dir() and any(events.glob("*.jsonl")):
            raise MigrateError(f"refused: target {name} is not empty ({events})")


def verify_targets(targets, source_count, manifest, by_project):
    """Re-read every written store; confirm chains, counts and src_sha coverage."""
    seen = {}
    for name, target in targets.items():
        lines = [x for x in target_file(target).read_bytes().split(b"\n") if x]
        prev = ""
        for index, raw in enumerate(lines, start=1):
            obj = json.loads(raw.decode("utf-8"))
            if obj["seq"] != index or obj["prev"] != prev:
                raise MigrateError(f"verify failed: chain broken {name}:{index}")
            prev = line_sha(raw)
        body = [json.loads(x.decode("utf-8")) for x in lines[1:-1]]
        if len(body) != by_project[name] or json.loads(lines[-1])["refs"]["count"] != len(body):
            raise MigrateError(f"verify failed: count mismatch in {name}")
        for ev in body:
            seen.setdefault(ev["refs"]["src_seq"], []).append((name, ev["refs"]["src_sha"]))
    if sorted(seen) != list(range(1, source_count + 1)) or any(len(v) != 1 for v in seen.values()):
        raise MigrateError("verify failed: a source seq is missing or duplicated")
    for row in manifest:
        if seen[row["src_seq"]] != [(row["project"], row["src_sha"])]:
            raise MigrateError(f"verify failed: src {row['src_seq']} misplaced")


def format_summary(prefix, n, by_project):
    return f"{prefix} source={n} " + " ".join(
        f"{p}={by_project[p]}" for p in ("aesop", "keyrun", "brain", "psinasty"))


# ------------------------------------------------------------------------ main

def parse_targets(pairs):
    targets = {}
    for pair in pairs:
        name, sep, path = pair.partition("=")
        if not sep or name not in PROJECTS or not path or name in targets:
            raise MigrateError(f"refused: bad --targets entry {pair!r}")
        targets[name] = Path(path)
    if set(targets) != set(PROJECTS):
        raise MigrateError("refused: --targets needs exactly aesop keyrun psinasty brain")
    return targets


def run(source, targets, dry_run):
    rows = read_source(source)
    require_frozen(rows)
    require_empty_targets(targets)
    manifest, per_project = build_plan(rows)
    by_project, by_rule = counts_of(manifest)
    n = len(rows)
    if dry_run:
        print("DRY RUN (nothing written)")
        print("per-rule: " + " ".join(f"{r}={by_rule[r]}" for r in RULES))
        print(format_summary("would migrate", n, by_project))
        return 0
    msha = manifest_sha_of(manifest)
    src_head = manifest[-1]["src_sha"]
    close_ts = rows[-1][1]["ts"]
    genesis_refs = {"src": SRC_LABEL, "src_head": src_head, "src_count": n,
                    "manifest_sha": msha}
    rendered = {p: render_store(per_project[p], genesis_refs, close_ts) for p in PROJECTS}
    for project, target in targets.items():
        path = target_file(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered[project])
    verify_targets(targets, n, manifest, by_project)
    doc = {"src": SRC_LABEL, "src_count": n, "src_head": src_head, "manifest_sha": msha,
           "counts": by_project, "rule_counts": by_rule, "rows": manifest}
    (Path(source) / "migration-manifest.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(format_summary("MIGRATION OK", n, by_project))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--targets", nargs="+", required=True, metavar="NAME=DIR")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(Path(args.source), parse_targets(args.targets), args.dry_run)
    except MigrateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
