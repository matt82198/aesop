# Incidents

[docs/INCIDENTS.md](../INCIDENTS.md) is a generated ledger of operational failures mined from git history.
This page says how to read it, what its counts are, and what it does not prove.

## How the ledger is made

`tools/incident_report.py` scans the history of the checked-out branch for failures and writes
`docs/INCIDENTS.md` (`tools/incident_report.py:4`). A commit may carry an `Incident-Class:` trailer, which is
authoritative; without one, the class is guessed from the commit subject and the row is marked `(inferred)`
(`tools/incident_report.py:9`; `docs/INCIDENTS.md:5`).

Check that the committed file matches what the generator produces now:

```
python tools/incident_report.py --check
```

<!-- CMD: python tools/incident_report.py --check
OUTPUT: OK: docs/INCIDENTS.md matches current state
EXIT: 0 -->

Expected: `OK: docs/INCIDENTS.md matches current state` and exit 0. A non-zero exit means the file drifted from
history.

## The counts

Every row in the committed ledger is currently marked inferred. None carries an explicit trailer. That makes
these counts a keyword classification of commit subjects, not a reviewed taxonomy.

<!-- CMD: grep -E '^\| [a-z-]+ \(inferred\)' docs/INCIDENTS.md | awk -F'|' '{print $2}' | sed 's/ (inferred) *//;s/^ *//' | sort | uniq -c
OUTPUT:
      1 ci-drift
     21 conflict
      2 doc-invented
      1 fake-green
      4 flake
      3 gate-activation
      4 stall
      5 test-pollution
-->
<!-- CMD: grep -cE '^\| [a-z-]+ \(inferred\)' docs/INCIDENTS.md
OUTPUT: 41 -->

| Class | Rows | Meaning (from the ledger's own summary, `docs/INCIDENTS.md:9-16`) |
|---|---|---|
| conflict | 21 | Merge or rebase conflict, module shadowing, unintended override |
| test-pollution | 5 | Test config leaked between shards, state not isolated |
| flake | 4 | Test timing or race condition |
| stall | 4 | Agent or process hung, watchdog detected |
| gate-activation | 3 | A pre-push secret or verification gate caught an escape or bypass |
| doc-invented | 2 | Documentation made unverifiable claims |
| ci-drift | 1 | CI workflow state out of sync |
| fake-green | 1 | Tests reported green but never ran real validation |
| Total | 41 | |

The ledger's last entry is dated 2026-09-02 (`docs/INCIDENTS.md:62`). The ledger does not list incidents
recorded elsewhere, such as the entries in the [gates fired](gates-fired.md) page that are dated later.

## Reading specific rows

- The fake-green row is `PR #464` (`docs/INCIDENTS.md:47`): browser-proof specs were not executing in CI. It is
  the only fake-green row. Do not cite it as one of several.
- The gate-activation rows are 3, not more: two secret-scan fixes and the gates-fired audit commit
  (`docs/INCIDENTS.md`, rows tagged `gate-activation`). Any other number you see quoted for gate
  activations is not what this ledger records.
- 21 of 41 rows are merge-conflict resolutions whose subject is the merge itself. They show how much merging
  the project does, and little else.

## Limits

- This is not a complete incident record. It holds what a keyword match found in commit subjects.
- Counts are rows in a table, not distinct root causes. Two rows may describe one event.
- It is a record from one project run by one maintainer. It does not support a rate comparison with any other
  project.
