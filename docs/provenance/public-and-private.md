# Public and private: what you can verify from this repository

Aesop's author develops it alongside three repositories that are not public. This page says which
parts of the system are in this repository, which are not, and what that means if you want to
reproduce the blueprint. It exists so that nothing on the other pages reads as shipped when it is not.

## The short version

- In this repository: the signed-receipt tools, the hash-chained event store, the 23-check pre-push policy,
  the lane contract text, three example Claude Code hooks, and a dated provenance record.
- Not in this repository: the Claude Code hooks that dispatch and track lanes, the hook that ties a
  "merged" or "green" statement to a same-turn read, the personal script library, and the operator
  tooling that mirrors them. These live in private repositories, named below.
- This repository is public: <!-- CMD: gh repo view matt82198/aesop --json visibility -> {"visibility":"PUBLIC"} -->
  `gh repo view matt82198/aesop --json visibility` reports `PUBLIC`.
- The license is PolyForm Noncommercial 1.0.0 (`LICENSE:1`; `package.json:67`). That is source-available,
  not an OSI open-source license. Do not read "public" as "open source".

## What the provenance record says

[docs/PROVENANCE.md](../PROVENANCE.md) is a table of 16 first-commit shas with UTC dates, signed by an
Ed25519 receipt ([docs/PROVENANCE.receipt.json](../PROVENANCE.receipt.json)).

<!-- CMD: grep -E '^\| [0-9]+ ' docs/PROVENANCE.md | cut -d'|' -f7 | sed 's/ *$//;s/^ *//' | sort | uniq -c
OUTPUT:
      5 aesop (public)
      9 claude-config (private)
      1 claude-scripts (private)
      1 conductor3 (private)
-->

| Repository (as named in the record) | Visibility | Rows in the record |
|---|---|---|
| aesop | public (this repository) | 5 of 16 |
| claude-config | private | 9 of 16 |
| claude-scripts | private | 1 of 16 |
| conductor3 | private | 1 of 16 |

Rows 1 and 4 to 11, 15 and 16 name a private repository (`docs/PROVENANCE.md:17,20-27,31-32`). A reader
without access to those repositories can check the record's signature and the sha values it
contains, but cannot open the commits behind the private rows. The record says so itself
(`docs/PROVENANCE.md:9-11`): it "does not open the source."

### What each private repository is, as far as this repository documents it

| Name | What the public tree says about it | Where |
|---|---|---|
| claude-config | Holds the Claude Code hooks listed in the record (lane ledger, handback ledger, verdict ledger, claim binding, context ceiling, checkpoint-before-compact) | `docs/PROVENANCE.md:20-27,31-32` |
| claude-scripts | Holds `dispatch_ledger.py` | `docs/PROVENANCE.md:25` |
| conductor3 | Holds the first hash-chained log (`tools/event_append.py`) and the private archive behind the cost dataset | `docs/PROVENANCE.md:17`; `docs/ab-cost-dataset.md:6` |

## What this means for each part of the blueprint

| Part | In this repository? | Evidence |
|---|---|---|
| Signed receipts: emit and verify offline | Yes | `tools/emit_receipt.py`, `tools/verify_receipt.py` (listed in `.github/CODEOWNERS:26-27`) |
| Provenance receipt verify | Yes | command below |
| Hash-chained event store (`prev` = sha256 of the previous event) | Yes | `tools/event_store/store.py:3-5`; `CHANGELOG.md` entry #1280 under 0.11.0 |
| Pre-push policy chain | Yes | 23 `check_*` functions in `hooks/pre-push-policy.sh` (count below) |
| Stage-lane contract text | Yes (text) | `docs/STAGE-CONTRACTS.md`, `LANE-CONTRACT.md` |
| Enforcement of the stage contracts at dispatch time | No. The enforcing hook is private. The public tree has the text and `tools/dispatch_lint.py` | `docs/PROVENANCE.md:20-27` names the lane hooks as private |
| Claim binding (a "merged" claim needs a same-turn read) | No. Brain-side pattern, not shipped. No public code or test | `docs/PROVENANCE.md:24` |
| Example Claude Code hooks | Three | `ls hooks/claude` (output below) |
| Personal script library | No | `docs/PROVENANCE.md:25` |

<!-- CMD: grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh
OUTPUT: 23 -->
<!-- CMD: ls hooks/claude
OUTPUT:
force-model-policy.mjs
no-heredoc-file-authoring.mjs
no-heredoc-file-authoring.test.mjs
no-scrollback-as-record.mjs
no-scrollback-as-record.test.mjs
-->

The three shipped Claude Code hooks are `force-model-policy.mjs`, `no-heredoc-file-authoring.mjs` and
`no-scrollback-as-record.mjs`. Two of them have tests in the same folder. The wiring into your Claude Code
settings is yours to do (`hooks/claude/force-model-policy.mjs:1-5`).

## Verify the provenance record yourself

From the repository root, with Python and the `cryptography` package installed:

```
python -I tools/emit_provenance_receipt.py --verify docs/PROVENANCE.receipt.json --subject docs/PROVENANCE.md --keyring tools/receipt_pubkeys
```

<!-- CMD: python -I tools/emit_provenance_receipt.py --verify docs/PROVENANCE.receipt.json --subject docs/PROVENANCE.md --keyring tools/receipt_pubkeys
OUTPUT: provenance receipt VALID (subject=docs/PROVENANCE.md)
EXIT: 0 -->

Expected output: `provenance receipt VALID (subject=docs/PROVENANCE.md)` and exit code 0. Exit codes are
0 for emitted or valid, 1 for invalid, 2 for cannot evaluate (`tools/emit_provenance_receipt.py:3`).

What a VALID result means: the bytes of `docs/PROVENANCE.md` are the bytes the key holder signed, and the
signature checks against a key registered under `tools/receipt_pubkeys/`. What it does not mean: it does not
check the commit shas against the repositories, and it says nothing about the private code
(`docs/PROVENANCE.md:51-59`).

Do not edit `docs/PROVENANCE.md`. Any change to its bytes makes the signature INVALID.

## Known limits of this page

- The author has not decided whether to open the private repositories. Until that is decided, treat
  every private-row primitive as described but not reproducible by you.
- The public key file under `tools/receipt_pubkeys/` carries a machine-specific filename. It is a public key,
  not a secret, and the name is left as committed.
- The cost figures in [Design decisions](design-decisions.md) trace to a private archive and are
  author-reported.
