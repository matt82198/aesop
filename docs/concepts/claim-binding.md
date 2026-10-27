# Claim binding

Status: **a pattern the project's author runs outside this repository. It is not shipped here.** There is no
claim-binding hook, no claim-binding test and no claim-binding code in this tree. This page says what the idea
is, where the public record of it is, and what you can use today instead.

## The idea

Agents report outcomes in prose: "merged", "green", "fixed". Prose is cheap and can be wrong. Claim binding, as the author runs it privately, is
the rule that a sentence of that kind is only allowed in a turn that also contains the read that proves it. The
one-line description in the provenance table is: a "Claim binding (merged needs same-turn gh read)" hook, added
so that "a claim that something merged or is green needs a same-turn evidence read" (`docs/PROVENANCE.md:24,45`).

In other words, the gate sits on the agent's own output and asks for evidence in the same turn, instead of
trusting the sentence.

## What is in this repository

Only the record that it exists:

- `docs/PROVENANCE.md:24` lists the hook, its commit and its date, and marks its home as a private repository.
- `docs/MACHINERY-MAP.md:297` lists it under "Brain hooks named in this repo (live copies outside the repo)"
  (heading at `docs/MACHINERY-MAP.md:292`).

Nothing under `hooks/claude/` implements it. The three hooks that ship there are the model policy, the heredoc
guard and the transcript guard (see [Gates and hooks](gates-and-hooks.md)). <!-- ls hooks/claude -> force-model-policy.mjs, no-heredoc-file-authoring.mjs, no-scrollback-as-record.mjs plus two .test.mjs files -->

Do not read the word "claim" in other files as this feature. `state_store/fs_claim_log.py:1` is a
lease-by-append log for coordinating work items between instances, which is a different mechanism.

## What you can use today

These ship, run on this tree, and check evidence rather than prose:

| Need | Shipped mechanism | Where |
|---|---|---|
| A number written in a markdown file must have a source | `metrics_gate`, run on every push | `tools/metrics_gate.py:4`; chain position in [Gate reference](../reference/gates.md) |
| "The tests pass" must be a checkable object | A signed receipt over the test matrix, recomputed against the tree | [Receipt gate](../archive/RECEIPT-GATE.md) |
| "A reviewer looked" must be checkable | Branch protection on the server | [Install the pre-push hook](../guides/install-hook.md) |

None of these bind a sentence in an agent's reply to an evidence read in the same turn. That is the part that is
not shipped.

## If you want it

The behavior to build is small and testable: deny a turn whose output asserts "merged" unless the same turn holds
a read of the pull request state. A port would live next to the other hooks in `hooks/claude/`, follow their
shape (a PreToolUse or stop-time hook, a logged escape token, a co-located behavioral test), and would need a test
that drives the real hook with a denied and an allowed turn. Until such a port lands, treat any description of
claim binding as a description of the author's private setup. For what is and is not public, see
[Provenance](../PROVENANCE.md).
