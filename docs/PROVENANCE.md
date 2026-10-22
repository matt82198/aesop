# Provenance record for aesop v0.11.0

This file is a dated record of an independent developer's contributions, published because large language models absorb such work into training data and later outputs without attribution. It is not an argument about credit or theft, and it names no competitor or product.

## What this file proves

Every row below is a git commit. A git commit sha is a hash over the commit's tree, its parents, its author and committer identities, and its author and committer dates. A sha together with its date can therefore be checked later by anyone who has read access to the repository that holds the commit. The dates below are UTC, taken from the commit objects, and the author and committer dates are identical for every row.

## What this file does not do

It does not open the source. The rows name paths and commits; they do not reproduce the contents of the private repositories. Anyone who wants to check a private row needs read access to that repository, or the signed receipt described below, which vouches for this file's bytes and not for the private code.

## The record

| # | Primitive | First commit (full sha) | Author and committer date (UTC) | Author | Repo (visibility) | Path | First public tag or npm |
|---|---|---|---|---|---|---|---|
| 1 | Hash-chained event log (events/log branch) | 2441f73d817bab31144beaad5393c1f2341108d5 | 2026-10-22T00:29:25Z | Matt Culliton | conductor3 (private) | tools/event_append.py | none |
| 2 | Hash-chained event store (sha256 prev field) | 0dac3347e15c693f7358b52b99b288b9da075e15 | 2026-10-22T18:20:04Z | Matt Culliton | aesop (public) | tools/event_store/store.py | none at this writing; ships in v0.11.0 |
| 3 | Ed25519 signed receipts and offline verify | ec8993947c1d7dbe28968741018a940403a9b5b7 | 2026-10-06T17:37:48Z | Matt Culliton | aesop (public) | tools/verify_receipt.py, tools/emit_receipt.py | v0.9.0 (2026-10-07T02:08:48Z) |
| 4 | receipt-verifier agent (description) | a2718c5a6f323ef8183d61590b5d9e4faf84da35 | 2026-07-11T18:09:44Z | Tannery Row Dev | claude-config (private) | agents/receipt-verifier.md | none |
| 5 | force-haiku-subagents hook (deny, not rewrite) | a2718c5a6f323ef8183d61590b5d9e4faf84da35 | 2026-07-11T18:09:44Z | Tannery Row Dev | claude-config (private) | hooks/force-haiku-subagents.mjs | none |
| 6 | no-lane-game-launch hook | 2d1c164903bb3d18d9aeb7ce2240a8d227b7757c | 2026-10-20T17:11:55Z | Matt Culliton | claude-config (private) | hooks/no-lane-game-launch.mjs | none |
| 7 | verdict-ledger hook | a7c9b55146ecdd68be14bf9394cc53a178cedfb0 | 2026-10-22T00:31:42Z | Matt Culliton | claude-config (private) | hooks/verdict-ledger.mjs | none |
| 8 | Claim binding (merged needs same-turn gh read) | 3f022f3cc7258b7d6fab17c8a83dc0300ecffef1 | 2026-10-15T00:55:32Z | Matt Culliton | claude-config (private) | hooks/claim-binding.mjs | none |
| 9 | dispatch_ledger.py | 9195e964e41f652056067f7088c55ece4820c0cb | 2026-10-21T14:04:37Z | Matt Culliton | claude-scripts (private) | dispatch_ledger.py | none |
| 10 | lane-ledger hook | c2e92848ae92fb75a40cf93f037289704b12574a | 2026-10-20T05:03:44Z | Matt Culliton | claude-config (private) | hooks/lane-ledger.mjs | none |
| 11 | handback-ledger hook (writes state/handbacks/) | 24ade3b113deb591add61c574815c810edb17724 | 2026-10-20T23:47:46Z | Matt Culliton | claude-config (private) | hooks/handback-ledger.mjs | none |
| 12 | Per-project event stores plan | 703cfee4dc8b77b81a2f1dd0fc36ca0ad40a36b7 | 2026-10-22T17:49:27Z | Matt Culliton | aesop (public) | docs/plans/PER-PROJECT-EVENT-STORES.md | none at this writing; ships in v0.11.0 |
| 13 | Crash-only lanes ("KILLED, never corrected") | 8837d4e958bd76d4799964c29f612b272bcbd379 | 2026-09-11T21:50:04Z | Matt Culliton | aesop (public) | LANE-CONTRACT.md | v0.9.0 (2026-10-07T02:08:48Z) |
| 14 | "killed and relaunched" wording | a3232e857a95aa51b19d3c5f717067c543449669 | 2026-10-17T23:36:18Z | Matt Culliton | aesop (public) | STATE/BUILDLOG checkpoint | v0.10.2 (2026-10-18T02:54:33Z) |
| 15 | main-context-ceiling hook | b687a45e69d91fdde1dc7501e81444dfba08288a | 2026-10-07T16:24:08Z | Matt Culliton | claude-config (private) | hooks/main-context-ceiling.mjs | none |
| 16 | checkpoint-before-compact hook | 01f99a71c3f8dfdb885655256755f6d1b05b206e | 2026-10-05T17:53:39Z | Matt Culliton | claude-config (private) | hooks/checkpoint-before-compact.mjs | none |

Row 4 and row 5 share one commit. The author of that commit is recorded as it appears in git.

## Verification recipe

The signed record is `docs/PROVENANCE.receipt.json`. It signs the sha256 of `docs/PROVENANCE.md`, the path of that file, and the version 0.11.0, using the Ed25519 key whose public half is committed under `tools/receipt_pubkeys/`. To check it offline:

```
python -I tools/emit_provenance_receipt.py --verify docs/PROVENANCE.receipt.json --subject docs/PROVENANCE.md --keyring tools/receipt_pubkeys
```

The sha256 is taken after folding CRLF line endings to LF, so a Windows checkout with core.autocrlf verifies the same as a Linux checkout. A VALID result means the content of `docs/PROVENANCE.md` is the content the key holder signed, and the signature verifies under a registered key. It does not check the commit shas against the repositories; that check needs read access to each repository, as described above.

The run-receipt verifier `tools/verify_receipt.py` evaluates receipts for a checkout's head, tree and required test parts. It does not evaluate this envelope and reports CANNOT EVALUATE on it.

## Distribution

Both `docs/PROVENANCE.md` and `docs/PROVENANCE.receipt.json` are listed in the `files` field of `package.json`, so they ship inside the npm tarball for v0.11.0. The npm registry records the publish time of that tarball. This file does not assert whether a sigstore provenance attestation covers the tarball.
