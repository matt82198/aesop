# Stage matrix

This reference lists what each stage lane may take in, produce, run, and hand back, for
each generic change category. It is the lookup table behind [stage lanes](../concepts/stage-lanes.md).

Each row cites the line it comes from, in the form `path:line`. Paths are relative to
the repository root.

## Scope of this table

The table covers the generic pipeline only.

- Included categories: code change, docs change, and CI fix. See the category notes
  below.
- Omitted: two stages (S0 evidence capture and S5 validate) that exist only for one
  project-specific bugfix category (`docs/STAGE-CONTRACTS.md:11`,
  `docs/STAGE-CONTRACTS.md:17`).
- Omitted: the categories tied to one project's game code, its memory store, and its
  private brain tooling (`docs/STAGE-CONTRACTS.md:24-27`, `docs/STAGE-CONTRACTS.md:29-30`).

## Category notes

| Generic category | Source category in the contract | Source |
|---|---|---|
| code change | `hook` and `script` | `docs/STAGE-CONTRACTS.md:21-22` |
| docs change | `docs` | `docs/STAGE-CONTRACTS.md:23` |
| CI fix | `ci-fix`, one row of the CI catalog | `docs/STAGE-CONTRACTS.md:28`, `docs/CI-CATALOG.md` |

The contract's `hook` and `script` categories are merged here because their stage verbs
match. Both are test-first and both run a named test. The contract's `hook` category
names a separate tooling repository. That name is not used on this page.

## Stage by category

Stage verbs are abbreviated. The source line for each row gives the exact text.

| Stage | Code change | Docs change | CI fix |
|---|---|---|---|
| **S1 SPEC** | IN: a row or ruling. OUT: a failing test path and its red output. ALLOWED: read, search, write the test file, run that one test. FORBIDDEN: editing non-test files, commit, push. HAND BACK: test path and red lines. (`docs/STAGE-CONTRACTS.md:12`, `:51`) | SKIPPED. Docs-only changes skip S1. (`docs/STAGE-CONTRACTS.md:54`) | IN: the catalog row. OUT: a repro path. Must reproduce the red from the catalog recipe. FORBIDDEN: editing gates, baselines, or timeouts. (`docs/STAGE-CONTRACTS.md:64-65`) |
| **S2 IMPL** | IN: the S1 test path and red output. OUT: a diff file, no commits. Makes that test green and nothing more. FORBIDDEN: editing the test, committing, pushing. HAND BACK: diff path and the green line. (`docs/STAGE-CONTRACTS.md:13`, `:72-73`) | IN: the brief. OUT: the one named doc. FORBIDDEN: edits outside the named doc, commit, push. HAND BACK: doc path and line count. (`docs/STAGE-CONTRACTS.md:76-77`) | IN: the catalog row. OUT: a diff file. Applies the catalog recipe once, then runs the S1 repro, which must pass. FORBIDDEN: raising timeouts, hiding new keys in baselines, a second retry. (`docs/STAGE-CONTRACTS.md:86-87`) |
| **S3 GATE** | IN: the S2 diff. OUT: a gate log file. Runs the test read-only. FORBIDDEN: Edit, Write, commit, push. HAND BACK: PASS line or recipe id. (`docs/STAGE-CONTRACTS.md:14`, `:94-95`, `:96-97`) | Runs the secret scan only. Fast check is skipped for docs. HAND BACK: CLEAN line with a non-zero file count. (`docs/STAGE-CONTRACTS.md:98-99`, `:171`) | Runs the gate named in the CI log, read-only. FORBIDDEN: Edit, Write, `--update-baseline`. HAND BACK: PASS line or the catalog row id. (`docs/STAGE-CONTRACTS.md:108-109`) |
| **S4 SHIP** | IN: the S3 PASS and a message file. OUT: PR number, head sha, armed state. Commit, foreground push, open PR against main, arm auto-merge. FORBIDDEN: code edits, `--no-verify`, update-branch, manual merge. (`docs/STAGE-CONTRACTS.md:116-117`) | Commit, foreground push, open PR, arm auto-merge. No edits. (`docs/STAGE-CONTRACTS.md:120-121`) | Push the fix, or merge forward only when the catalog row says so, then arm. (`docs/STAGE-CONTRACTS.md:130-131`) |
| **S5 REVIEW** | IN: the S4 PR diff and the ruling. OUT: a review file. Read-only. HAND BACK: verdict and refutations, each with `file:line`. (`docs/STAGE-CONTRACTS.md:16`, `:138-139`) | Optional. Refutes the doc diff against the brief. (`docs/STAGE-CONTRACTS.md:142-143`) | Refutes the fix against the catalog row. FORBIDDEN: baseline edits. (`docs/STAGE-CONTRACTS.md:152-153`) |

## Pipeline rules that apply to every row

- The orchestrator runs S1, then S2, then S3, then S4. S5 runs when the orchestrator
  names it (`docs/STAGE-CONTRACTS.md:168`).
- A stage starts only after the previous hand-back names its output artifact, a path or
  a sha (`docs/STAGE-CONTRACTS.md:170`).
- A brief names exactly one stage's verbs (`docs/STAGE-CONTRACTS.md:172`).
- The contract says a branch with a merge-forward commit is scanned with the range form,
  and that the staged form does not inspect anything once the index equals HEAD
  (`docs/STAGE-CONTRACTS.md:15`). The contract's wording is from a different copy of the
  scanner. This repository's `tools/secret_scan.py` documents a range mode,
  `--range COMMIT_RANGE`, and says an empty index is EMPTY-INDEX with exit 1 unless
  `--allow-empty` is given (source: `python tools/secret_scan.py --help`). Use the range
  form on a branch that is ahead of its base.

## Verifying the table against the source

Every row above cites a line range in `docs/STAGE-CONTRACTS.md`. To check a row, read
the cited line and compare the verbs. The table is generated from the contract by hand,
so a change to the contract requires a change to this table.
