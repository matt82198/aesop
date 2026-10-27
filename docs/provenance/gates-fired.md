# Gates fired

A guardrail that has never blocked anything is unproven. [docs/GATES-FIRED.md](../GATES-FIRED.md) is the
project's attempt to list which gates have a recorded blocking event. This page says what that file
holds, re-checks what can be re-checked, and states where it disagrees with the current tree.

## What the ledger claims

The ledger's summary (`docs/GATES-FIRED.md:9-12`) gives four figures:

| Figure | Value in the ledger |
|---|---|
| Gates with a documented activation (PROVEN) | 8 |
| Gates implemented with no documented blocking event (UNPROVEN) | 14 |
| Gates that skip themselves when their tool file is missing (FAIL-OPEN) | 7 |
| Total | 22 |

The ledger does not agree with itself. Its summary says 14 unproven gates, its section heading says 15
(`docs/GATES-FIRED.md:72`), and its closing note says "7 is the actual count" of proven gates
(`docs/GATES-FIRED.md:81`) while the heading says 8 (`docs/GATES-FIRED.md:14`). Read the figures as
a draft count, not a reconciled one.

## The proven entries, by name

These are the eight headings under "Proven Activations" (`docs/GATES-FIRED.md:16-42`):

1. `secret_scan.py`, several blocks
2. `encoding_lint.py`, blocked a set of lanes until mechanical fixes
3. `verify_test_coverage.py`, found orphaned shell tests
4. `verify_dash.py`, browser proofs that were not running
5. `self_stats.py --check`, README stats drift
6. `state_md_verifier.py`, a gate that reported success while verifying nothing
7. Branch protection on main
8. `ci_workflow_lint.py`, a workflow GitHub rejected while the local gate printed OK

Entries 4, 6 and 8 are headed as fake-green catches: the gate itself was the defect. For entries 6 and 8 the
ledger says the fix is the evidence (`docs/GATES-FIRED.md:37,44`). That is a different thing from a gate
catching someone else's mistake.

## Re-checking the citations

The ledger's standard is that every proven entry cites a commit, a PR or a run
(`docs/GATES-FIRED.md:5`). Re-checking the cited identifiers against this repository's history:

<!-- CMD: for c in dc76586 de1ddac ef491c7 3753a01 ff514eaa; do printf '%s ' $c; git cat-file -t $c 2>&1 | head -1; done
OUTPUT:
dc76586 commit
de1ddac commit
ef491c7 fatal: Not a valid object name ef491c7
3753a01 commit
ff514eaa fatal: Not a valid object name ff514eaa
-->
<!-- CMD: for n in 464 636 638 850; do git log --oneline --grep="(#$n)" | head -1; done
OUTPUT:
7e20522e ci(browser-proofs): actually execute playwright specs + minimal dashboard smoke (#464)
2418634d fix: add encoding=utf-8 to all subprocess calls (62 violations) (#636)
5ad3ed47 fix/state md verifier failclosed (#638)
2eff4071 ci: optimize throughput with PR head-SHA checkout and Windows path gating (#850)
-->

- Of the five commit shas cited, three resolve (`dc76586`, `de1ddac`, `3753a01`). Two do not resolve in this
  repository: `ef491c7` (`docs/GATES-FIRED.md:19`) and `ff514eaa` (`docs/GATES-FIRED.md:22`).
- The pull-request numbers #464, #636, #638 and #850 each match a commit subject ending in that number.
  The ledger gives a merge sha for #850 (`f8c898de`, `docs/GATES-FIRED.md:43`) that does not resolve either.
  The PR number does.
- Until the two unresolved shas are replaced with ones that resolve, treat entries 1 and 2 as
  partly supported: the other cited commits and PRs under those entries still resolve.

## The self-skipping checks

The ledger lists seven pre-push checks that skip themselves when their tool file is absent, with line numbers
(`docs/GATES-FIRED.md:49-60`). Those line numbers were taken on an older version of
`hooks/pre-push-policy.sh` and no longer point at the same code. The principle still appears in the
current file: its comments say "Fail-open ONLY for missing optional tooling" in several places
(`hooks/pre-push-policy.sh:890,1034`), and skips are logged
(`hooks/pre-push-policy.sh:1660,2216`).

The ledger names a risk with this design (`docs/GATES-FIRED.md:62-70`): if a binary is removed involuntarily,
for example by endpoint security software, a skipped gate looks the same in the audit log as a deliberate
skip. If you adopt the pre-push policy, make sure the tools it expects are present, and read the audit log for
`skipped_tool_missing` events.

<!-- CMD: grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh
OUTPUT: 23 -->

The current pre-push policy defines 23 `check_*` functions, not the 22 gates the ledger totals. The two figures
count different things and the ledger has not been regenerated. Do not quote 22 as a current count.

## Limits

- The ledger was last changed on 2026-10-06 (`git log -1 --format=%ad --date=short -- docs/GATES-FIRED.md`),
  before the current version of the policy file.
- It records blocking events that someone wrote down. A gate with no entry may have fired and not been logged.
- It is a single maintainer's record. Nothing here is an independent audit.
