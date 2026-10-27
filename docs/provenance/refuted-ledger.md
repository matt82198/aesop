# The refuted ledger and other evidence

Git records what shipped. The refuted ledger records ideas and claims that were measured and killed.
It is [docs/refuted.jsonl](../refuted.jsonl): one JSON object per line, validated by
`tools/refuted_ledger.py`. The rules for it are in [docs/REFUTED.md](../REFUTED.md). This page
shows how to validate it yourself and what its numbers do and do not say.

## Validate it

From the repository root:

```
python tools/refuted_ledger.py validate docs/refuted.jsonl
python tools/refuted_ledger.py stats docs/refuted.jsonl --json
```

<!-- CMD: python tools/refuted_ledger.py validate docs/refuted.jsonl
OUTPUT: OK 36 entries in docs\refuted.jsonl
EXIT: 0 -->

Expected from `validate`: `OK 36 entries in` followed by the path, and exit 0. Exit 1 means validation
refused and the line number is printed; exit 2 means a usage or I/O error (`docs/REFUTED.md`, "Commands").
Validation refuses a file with unknown keys, a bad date, or evidence that is a personal path.

## The numbers

<!-- CMD: python tools/refuted_ledger.py stats docs/refuted.jsonl --json
OUTPUT (abridged):
by_measured_by: ab 0, gate 5, precedent 7, repro 3, review 19, test 2
by_proposed_by: hook 6, lane 16, orchestrator 11, user 3
by_verdict: DEFERRED 7, DONE_BY_DESIGN 1, FALSE_POSITIVE 4, REFUTED 24
refute_rate: 0.7778
total: 36
-->

| Verdict | Entries |
|---|---|
| REFUTED | 24 |
| DEFERRED | 7 |
| FALSE_POSITIVE | 4 |
| DONE_BY_DESIGN | 1 |
| Total | 36 |

The ledger defines the refute rate as (REFUTED + FALSE_POSITIVE) divided by the total (`docs/REFUTED.md`,
"Verdicts"). By that definition the figure is (24 + 4) / 36, which the tool reports as 0.7778.

How to read that:

- The ledger is about the project's own proposals, claims and gates. A high rate means the project writes down
  what it got wrong. It is not a quality score and is not comparable with another project's.
- Nineteen of the 36 entries were measured by `review`, two by `test`, and none by `ab`. The A/B cost runs behind
  one design decision are not in this ledger (see [Design decisions](design-decisions.md)).
- Entries date from 2026-07-22 to 2026-10-15. The ledger is not a complete log of every failure. Use the
  [incident ledger](incidents.md) for that, with its own caveats.
- Every entry carries a public pointer as evidence (a PR, a commit, a run, a repo file). The validator checks
  that the pointer is not a personal path. It does not check that the pointer resolves, so resolve a few
  yourself before you rely on the ledger.

## The rule the ledger follows

"Show all of it" (`docs/REFUTED.md`, "The rule"): hook false positives and the orchestrator's own refuted ideas
are included, and nothing is removed for looking bad. This repository cannot prove that nothing was left out.
What it can show is that every entry that is present passes the validator, and that entries are appended by
the validated `add` command (`tools/refuted_ledger.py:307`).

## Other evidence in this repository

| Artifact | What it is |
|---|---|
| [docs/paper/EVIDENCE.md](../paper/EVIDENCE.md) | The evidence base compiled for the paper draft, with dates from commit objects |
| [docs/recordings/](../recordings/) | A recorded non-Claude end-to-end run and the script that produces it |
| [docs/templates/](../templates/) | Example fleet-ops analysis and proposal templates |
| [docs/RECEIPTS.md](../RECEIPTS.md) | Per-wave metrics from an earlier snapshot. This is not the signed receipt format, despite the shared word |

Signed receipts are described and verified through `tools/verify_receipt.py` and `tools/emit_receipt.py`; see
[Public and private](public-and-private.md) for the one receipt you can verify without any other setup.

## Limits

- 36 entries from one maintainer's project is a small sample.
- The ledger shows that claims were corrected. It does not show the claims that were never checked.
