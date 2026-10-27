# Provenance and evidence

This folder answers one question for a stranger: what here can I check for myself, and how?

Aesop is source-available (see [LICENSE](../../LICENSE)). The pages below separate what is in this public
repository from what is not, and point at the ledgers the project keeps about its own failures.

| Page | What it tells you |
|---|---|
| [Public and private](public-and-private.md) | Which parts of the system ship in this repo and which live in repositories you cannot read |
| [Design decisions](design-decisions.md) | The recorded architecture decisions and the cost model, with what is and is not reproducible |
| [Incidents](incidents.md) | The incident ledger, with counts re-derived from it |
| [Gates fired](gates-fired.md) | Which guardrails have a recorded blocking event, and the limits of that record |
| [Refuted ledger](refuted-ledger.md) | Ideas and claims that were measured and killed, and how to validate the ledger |

## How to read the evidence on these pages

- A `path:line` cite points at a line in this repository at the commit you have checked out. Lines drift
  when files change, so treat a cite as a pointer and confirm the quoted text.
- A command shown in an HTML comment next to a number is the command that produced it, with its literal
  output. View the page source (or the raw file) to see it, and re-run the command from the repository root.
- A statement with no cite and no command is a pointer to another page, not a claim.

## What these pages do not claim

- They do not claim that every hook the author runs is in this repository. Several are not (see
  [Public and private](public-and-private.md)).
- They do not claim that the event log is tamper-proof. The shipped event store is hash-chained, which makes
  edits detectable (tamper-evident) to anyone who re-verifies the chain.
- They do not claim a measured cost advantage that you can reproduce from this repository alone
  (see [Design decisions](design-decisions.md)).
