"""Projections over per-project event stores (G1.1 L5).

Read-only and deterministic. Every view reads the stores named in the registry, verifies each
store's chain first, and renders events only. A store that cannot be read, or whose chain is
broken, is reported on stdout as `store <p> unreadable: <reason>` or `CHAIN BROKEN <p>: <problem>`;
the other stores still render. The caller exits 1 when anything was reported.

Views:
  pending      verdict/finding events with refs.observable that no finding closes. A finding
               closes an item in the same store by refs.closes (observable or sha prefix), or
               from any store by refs.xref = "<project>:<seq>".
  rulings      ruling events, oldest first across stores. --by-goal groups them by refs.goal.
  checkpoints  --last N (newest N across stores), --last N --per-store (newest N of each store),
               or --per-goal (newest checkpoint per refs.goal).
"""
import json

from event_store import registry as reg_mod
from event_store.store import Store, StoreError, line_sha

OPEN_KINDS = ("verdict", "finding")
CLOSE_PREFIX_MIN = 8
NO_GOAL = "(no goal)"


def _raw_lines(store):
    out = []
    for day in store.day_files():
        out.extend(x for x in day.read_bytes().split(b"\n") if x)
    return out


def load_rows(reg, projects):
    """Return (rows, problems) for the named projects, in the given order.

    A row is {"project", "seq", "sha", "event"} in store file order. Problems are
    preformatted report lines for stores that were skipped.
    """
    rows, problems = [], []
    for project in projects:
        try:
            store = Store(reg_mod.store_path(reg, project))
        except (reg_mod.RegistryError, StoreError) as exc:
            problems.append("store %s unreadable: %s" % (project, exc))
            continue
        if store.project != project:
            problems.append("store %s unreadable: store belongs to %r" % (project, store.project))
            continue
        broken = store.verify()
        if broken:
            problems.extend("CHAIN BROKEN %s: %s" % (project, b) for b in broken)
            continue
        for raw in _raw_lines(store):
            event = json.loads(raw.decode("utf-8"))
            rows.append({"project": project, "seq": event["seq"],
                         "sha": line_sha(raw), "event": event})
    return rows, problems


def _order(row):
    return (row["event"]["ts"], row["project"], row["seq"])


def _refs(row):
    return row["event"].get("refs") or {}


def _goal(row):
    return _refs(row).get("goal", "")


def _one_line(text):
    return text.replace("\r", " ").replace("\n", " ")


def _suffix(row):
    goal = _goal(row)
    return " [project=%s]" % row["project"] + (" [goal=%s]" % goal if goal else "")


def _ruling_line(row):
    ev = row["event"]
    return "%s %s %s%s" % (ev["ts"], ev["actor"], _one_line(ev["text"]), _suffix(row))


def _checkpoint_header(row):
    ev = row["event"]
    return "# %s %s%s" % (ev["ts"], ev["actor"], _suffix(row))


def _closes(closes, sha, observable):
    if not closes:
        return False
    if closes == observable:
        return True
    return len(closes) >= CLOSE_PREFIX_MIN and sha.startswith(closes)


def _is_closed(row, rows):
    obs = _refs(row).get("observable", "")
    target = "%s:%d" % (row["project"], row["seq"])
    for other in rows:
        ev = other["event"]
        if ev["kind"] != "finding":
            continue
        refs = _refs(other)
        if refs.get("xref") == target:
            return True
        if (other["project"] == row["project"] and other["seq"] > row["seq"]
                and _closes(refs.get("closes", ""), row["sha"], obs)):
            return True
    return False


def pending(rows):
    """Open verdict/finding items, in store order then project order."""
    out = []
    for row in rows:
        ev = row["event"]
        if ev["kind"] not in OPEN_KINDS or not _refs(row).get("observable"):
            continue
        if not _is_closed(row, rows):
            out.append(row)
    return out


def rulings(rows):
    return sorted((r for r in rows if r["event"]["kind"] == "ruling"), key=_order)


def checkpoints(rows):
    return sorted((r for r in rows if r["event"]["kind"] == "checkpoint"), key=_order)


def last_checkpoints(rows, count):
    """The newest `count` checkpoints across all stores."""
    if count <= 0:
        return []
    return checkpoints(rows)[-count:]


def last_checkpoints_per_store(rows, count):
    """The newest `count` checkpoints of each store, stores in name order."""
    if count <= 0:
        return []
    out = []
    for project in sorted({r["project"] for r in rows}):
        mine = [r for r in checkpoints(rows) if r["project"] == project]
        out.extend(mine[-count:])
    return out


def latest_checkpoint_per_goal(rows):
    """The newest checkpoint for each refs.goal, goals sorted, no-goal group last."""
    latest = {}
    for row in checkpoints(rows):
        goal = _goal(row)
        latest[goal] = row  # checkpoints() is oldest first, so the last write wins
    return [latest[g] for g in sorted(latest, key=lambda g: (g == "", g))]


def _render_rows(rows, render):
    return [render(r) for r in rows]


def _render_by_goal(rows):
    groups = {}
    for row in rulings(rows):
        groups.setdefault(_goal(row) or NO_GOAL, []).append(row)
    lines = []
    for goal in sorted(groups, key=lambda g: (g == NO_GOAL, g)):
        lines.append("goal=%s" % goal if goal != NO_GOAL else NO_GOAL)
        lines.extend(_render_rows(groups[goal], _ruling_line))
    return lines


def render(view, reg, projects, last=0, per_store=False, per_goal=False, by_goal=False):
    """Return (exit_code, output_lines) for one projection view over the named stores."""
    rows, problems = load_rows(reg, projects)
    if view == "pending":
        lines = [("%s | %s | %s | observable: %s" % (
            r["event"]["ts"], r["event"]["actor"], _one_line(r["event"]["text"]),
            _refs(r)["observable"])) for r in pending(rows)]
    elif view == "rulings":
        lines = _render_by_goal(rows) if by_goal else _render_rows(rulings(rows), _ruling_line)
    else:
        if per_goal:
            chosen = latest_checkpoint_per_goal(rows)
        elif per_store:
            chosen = last_checkpoints_per_store(rows, last)
        else:
            chosen = last_checkpoints(rows, last)
        lines = []
        for r in chosen:
            lines.append(_checkpoint_header(r))
            lines.append(r["event"]["text"])
    lines = problems + lines
    return (1 if problems else 0), lines
