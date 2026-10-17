# The Refuted Ledger

Git records what shipped. The refuted ledger records the negative space: ideas that were measured and killed, claims that a gate bounced, and lanes that came back REFUTED with no PR. It is `docs/refuted.jsonl`, one JSON object per line, validated by `tools/refuted_ledger.py`.

## The rule

**Show all of it.** Hook false positives and the orchestrator's own refuted ideas are included. Nothing is curated out for looking bad. An entry that makes the system look wrong is the point of the file. Seeds are grounded in repo files only, and every entry cites its evidence.

## Schema

Each line has these keys. Unknown keys are rejected.

| Key | Required | Rule |
| --- | --- | --- |
| `id` | yes | `R-NNNN`, unique within the file |
| `date` | yes | real calendar date, `YYYY-MM-DD` |
| `proposed_by` | yes | `user`, `orchestrator`, `lane` or `hook` |
| `proposal` | yes | one sentence: the idea or claim that was measured |
| `measured_by` | yes | `test`, `repro`, `ab`, `review`, `gate` or `precedent` |
| `verdict` | yes | `REFUTED`, `DEFERRED`, `DONE_BY_DESIGN` or `FALSE_POSITIVE` |
| `evidence` | yes | a public pointer: a GitHub run id, PR number, commit sha, repo-relative file path, or a ledger line id such as `STATE.md:11 (D4)` |
| `note` | no | one sentence of context |

Evidence and every other value must not contain a personal path (a Windows user profile path, a `$HOME`-style path, or a POSIX home path) or a UUID (session ids). Evidence must not be an absolute, home-relative or drive path at all.

## Verdicts

- **REFUTED**: measured, and the idea or claim is false or fails. Example: a gate reported green while verifying nothing.
- **FALSE_POSITIVE**: a gate or hook blocked or flagged something that was actually fine. Counts toward the refute rate.
- **DEFERRED**: not refuted, but parked, held or reversible. Revisit when the stated condition changes.
- **DONE_BY_DESIGN**: looked like a defect, and was closed as intended behaviour with a stated precedent.

`stats` reports the refute rate as (REFUTED + FALSE_POSITIVE) / total.

## Commands

```
python tools/refuted_ledger.py validate docs/refuted.jsonl
python tools/refuted_ledger.py add docs/refuted.jsonl --proposed-by lane --proposal "..." --measured-by test --verdict REFUTED --evidence "PR #123"
python tools/refuted_ledger.py stats docs/refuted.jsonl --json
python tools/refuted_ledger.py redact raw.jsonl docs/refuted.jsonl
```

Exit codes: `0` success, `1` validation refused (the line number is printed), `2` usage or I/O error. Validation is fail-closed: a file that fails is not extended by `add`, and `stats` refuses an invalid ledger.

## How to add an entry

1. Confirm the evidence exists publicly (a PR, a run, a commit, a repo file) and write its pointer, not a local path.
2. Run `add` with all required flags. It validates the whole resulting file before it writes, and the id is assigned as the next `R-NNNN` when `--id` is omitted.
3. Run `validate` and commit the file with the change that produced the evidence.

To record a raw local log, run `redact` to turn it into a committable form first. Redaction replaces Windows user paths and home paths with `<home>` and UUIDs with `<session>`. A redacted local path is still not a public pointer, so `validate` rejects it as evidence and the evidence must be rewritten to a public pointer.
