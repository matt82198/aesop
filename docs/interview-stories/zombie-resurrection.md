# The Zombie Resurrection Gate

*We measured a 79% zombie rate -- 15 of 19 "open" items were already shipped.*

## Context

Aesop uses an event-sourced state layer to track work items across waves.
Each item in the tracker has a lane: `proposed`, `ranked`, `in-progress`,
`accepted`, `done`, or `rejected`. At the start of each wave, the orchestrator
reads the tracker to build a backlog: which items are still open? Which need
work? The tracker is the single source of truth for what the fleet should
work on next.

The event store records `item_created`, `item_updated`, and `item_archived`
events. A projection function folds these events into the current tracker
state -- a JSON file (`state/tracker.json`) that the wave loop reads. The
architecture is clean: append-only log as the source, materialized view as
the read model. Textbook event sourcing.

Except for one thing.

## The Bug

The tracker knew when items were *created* and when their metadata was
*updated*. But it had no reliable signal for when items were *completed*. An
agent would pick up an item, write the code, push a branch, create a PR, and
the PR would get merged. At that point, the work was done -- the code was on
`main`. But the tracker didn't know. No event was emitted for "this item's PR
merged." No event was emitted for "this item's owned files now exist on main."

The item stayed in whatever lane it was last seen in -- usually `in-progress`
or `ranked`. Wave after wave, the orchestrator would read these items, try to
dispatch agents to work on them, and either discover the work was already done
(wasted agent time) or, worse, re-create the work in a way that conflicted
with the already-merged code.

We discovered this at a wave boundary reconciliation. Out of 19 items showing
as "open" in the tracker, **15 were already shipped**. Their PRs had been
merged. Their code was on `main`. Their files existed. The tracker just didn't
know. That's a **79% zombie rate** -- nearly four out of five "open" items
were zombies, resurrected from the dead by an event store that only recorded
births, never deaths.

This wasn't a subtle data-quality issue. It meant the orchestrator was spending
the majority of its wave-planning effort on work that was already done. It
meant agents were being dispatched to fix bugs that were already fixed. The
backlog was fiction.

## Discovery

The discovery was a manual reconciliation during wave-1 of a `/afk` session
(an unattended autonomy run). The orchestrator was supposed to clear the
backlog autonomously, but it kept picking up items that had no remaining work.
Cross-referencing the tracker against `git log --oneline` and `gh pr list
--state merged` revealed the 15/19 split.

The reconciliation was initially a manual audit step -- a human looked at the
tracker, looked at the merged PRs, and marked items done. But a manual step
in an autonomous system is a contradiction. The whole point of aesop is
unattended operation. If the tracker requires a human to tell it when work is
done, the tracker is broken.

## The Fix

Two complementary mechanisms:

**1. `tracker_autoclose.py` (Guardrail G1)** -- an automatic gate that
cross-references the tracker against GitHub and git state. For each open item,
it checks: does the item link to a PR? Is that PR merged? Do the item's
`ownsFiles` exist on `main`? If either condition is met, the item is
auto-closed.

```python
# Pseudo-code for the auto-close logic:
for item in tracker.items:
    if item.lane in ACTIVE_LANES:
        if item.pr_number and gh_pr_state(item.pr_number) == "MERGED":
            close(item, reason="PR merged")
        elif item.ownsFiles and all_on_main(item.ownsFiles):
            close(item, reason="files shipped")
```

This runs at wave-open as a mandatory reconciliation step. It's the structural
fix -- items can't stay zombie because the system actively checks for their
completion.

**2. `tracker_guard.py`** -- an append-only lane journal that enforces a
one-way lifecycle. Once an item reaches a terminal lane (`done` or
`rejected`), it can never move back to an active lane (`ranked`, `proposed`,
`in-progress`, `accepted`). The journal records every lane transition in
`state/tracker-journal.jsonl`:

```json
{"ts": "2026-07-28T14:22:01", "id": "item-42", "from": "in-progress", "to": "done"}
{"ts": "2026-07-29T09:00:03", "id": "item-42", "from": "done", "to": "ranked", "type": "ZOMBIE"}
```

The guard has three modes: `--seed` (bootstrap the journal from current
state), `--check` (detect violations, exit 1 if found -- fail-closed), and
`--enforce` (revert any zombie items to their last terminal lane). The check
mode runs as a gate; the enforce mode runs as a fixer.

The combination is belt-and-suspenders: `tracker_autoclose` prevents zombies
from forming (by closing items when their PRs merge), and `tracker_guard`
prevents zombies from resurrecting (by enforcing the one-way lifecycle
invariant even if the auto-close is bypassed).

After deploying both, the reconciliation at wave-open showed the zombie rate
drop from 79% to 0%. The `reconcile-tracker-at-wave-open` rule became a
permanent part of the wave lifecycle, and the reconciliation step was added
to the `/buildsystem` skill's Phase 0.

## Design Lesson

Event-sourced systems are powerful because the log is the truth. But "the
log is the truth" only holds when the log captures the *complete* lifecycle
of each entity. If you only emit creation events, you've built half an event
store. The projection will show you everything that was ever started and
nothing that ever finished.

The specific trap here is that absence of a "done" event is ambiguous. It
could mean "not done yet" (the intended interpretation) or "done, but nobody
told the event store" (the actual situation 79% of the time). In a traditional
CRUD system, someone updates a status column and you see the current state.
In an event-sourced system, if the completion event is never emitted, the
projected state is permanently stale.

The fix isn't just "emit more events." It's recognizing that some lifecycle
transitions happen *outside* the event store's natural boundary. A PR merging
is a GitHub event, not an aesop event. A file appearing on `main` is a git
fact, not a tracker fact. The reconciliation gate bridges that boundary --
it translates external state (GitHub PR status, git file existence) into
internal events (tracker lane transitions).

> **Design Principle**
>
> In an event-sourced system, every entity needs **explicit lifecycle events**
> for every significant state transition -- especially termination.
>
> If a transition happens outside the event store's boundary (a PR merging
> in GitHub, a deploy completing in CI), you need a **reconciliation gate**
> that bridges external state into internal events. Without it, the absence
> of a "done" event is indistinguishable from "not done," and your backlog
> fills with zombies.
>
> Measure it: the zombie rate (items marked open that are actually complete)
> is a direct indicator of lifecycle-event coverage. If it's above 0%, your
> event model has a gap.
