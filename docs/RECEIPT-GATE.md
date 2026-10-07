# Receipt gate — receipts replace redundant CI

**Status:** increments 1–3 shipped (measurement period). The hosted `verify-receipt`
check is **not required**. Nothing in branch protection changes until increment 4.

## Why

Every aesop lane already runs the full local matrix before it pushes (shards, pre-push
gates). Hosted CI then runs the same matrix again on the same tree. The receipt gate
lets a lane *prove* what it ran — signed, bound to the exact tree — so that, once trust
is measured, the hosted re-run can become a sample instead of a tax.

## What ships now

| Increment | Piece | File |
|---|---|---|
| 1 | Emitter: run the local matrix, sign, post | `tools/emit_receipt.py` (+ `tools/receipt_common.py`) |
| 2 | Offline verifier | `tools/verify_receipt.py` |
| 3 | Hosted verifying Action (non-required) | `.github/workflows/verify-receipt.yml` |

**Emission is now automatic.** `hooks/pre-push-policy.sh`'s `check_emit_receipt()` runs
`tools/emit_receipt.py --post` on every push, once every gate above it has passed (see
hooks/CLAUDE.md item 16). A day-1 measurement of the manual-only design found 41 merged
PRs and ZERO receipts — lanes were never told to run the emitter and nothing ran it for
them, so the honesty signal had no data. The hook step is ALWAYS fail-open: a missing
tool/python/`gh`/signing-key, or a red matrix part, prints a one-line WARN and never
blocks the push. `AESOP_RECEIPT_EMIT=0` opts out entirely. Manual invocation still works
the same way, e.g. for a `--dry-run` preview or a narrower `--matrix`:

```
python tools/emit_receipt.py --post            # default matrix, Ed25519 if $AESOP_RECEIPT_KEY is set
python tools/emit_receipt.py --dry-run         # print the signed envelope, post nothing
python tools/emit_receipt.py --matrix py-shard-0,py-shard-1 --post
```

## The receipt

The receipt is **not a committed file** — a committed receipt would have to describe
the tree that contains it (self-reference) and would conflict on every merge. It is
posted on the **head sha** as a GitHub check-run named `verify-receipt-local`
(`output.text` carries the JSON) or, when the token cannot create check-runs (only
GitHub Apps may; a lane's user token gets 403), as a **commit comment** on the same sha
under the marker `<!-- aesop-receipt:verify-receipt-local -->`. The Action reads both.

```json
{"receipt": {
   "schema": 1, "repo": "owner/name",
   "head_sha": "...", "base_sha": "<merge-base with origin/main>",
   "tree_hash": "<git rev-parse HEAD^{tree}>",
   "parts":   [{"name": "py-shard-0", "exit_code": 0, "test_count": 212, "duration_s": 48.1}, ...],
   "skipped": [{"name": "browser-proofs", "reason": "not runnable here: ..."}],
   "host": {"os": "windows", "python": "3.12.4", "hostname_hash": "<sha256[:16]>"},
   "timestamp": "2026-10-06T14:02:11Z"},
 "sig": {"scheme": "ed25519", "value": "<base64>", "key_id": "<sha256(pubkey)[:16]>"}}
```

Canonical form: `json.dumps(receipt, sort_keys=True, separators=(",", ":"))`. The
signature covers the canonical bytes, so changing **any** field (an exit code, the tree,
the base) invalidates it.

Default matrix: `py-shard-0..3` (`tools/ci_shard_runner.py n 4`) plus the pre-push gate
set (`secret-scan`, `claudemd-sync-gate`, `gen-tool-index`, `verify-test-suite-count`,
`encoding-lint`, `import-resolution-check`, `sibling-import-check`). Parts that cannot
run on a dev box (`browser-proofs`, `windows-shard`) are recorded under `skipped` with a
reason — never silently dropped — and are *not* in the verifier's required list.

## Signing scheme

**Ed25519** (`cryptography`, optional import) is the scheme in use: the private key
lives at `$AESOP_RECEIPT_KEY` on the signing box and is never in the repo; the public
key is committed at `tools/receipt_pubkey.pub` (ssh `.pub` naming because
`tools/secret_scan.py` rejects every `*.pem` filename as credential-shaped, which is the
right default for a repo). Asymmetric matters: the verifier holds **no secret**, so a
leaked Actions config cannot forge receipts.

**HMAC-SHA256** (stdlib) is the fallback for a box without `cryptography`, keyed by
`$AESOP_RECEIPT_HMAC_SECRET` on both the signer and the Action (`secrets.
AESOP_RECEIPT_HMAC_SECRET`). It is symmetric — whoever can verify can also sign — so it
is strictly weaker; `tools/` must stay stdlib-only (tools/CLAUDE.md), which is why it
exists. A receipt names its scheme; the verifier fails closed on an unknown one.

Rotation: `receipt_common.generate_ed25519_keypair(priv, pub)`, commit the new `.pub`.

## Trust boundary — what a valid receipt proves, and what it does not

Proves (checked by `verify_receipt.py`, each independently, fail-closed):

1. **Signature** — the holder of the private key produced this exact receipt.
2. **Tree binding** — `tree_hash` equals `git rev-parse <head>^{tree}` **recomputed from
   the verifier's own checkout**. The lane's number is never trusted; a receipt for a
   different tree (or a later amend) fails.
3. **Head binding** — `head_sha` equals the checkout head (the Action checks out the PR
   head sha, not the synthetic merge commit).
4. **Freshness** — `base_sha` is an ancestor of `origin/main` and at most `--max-behind`
   (50) commits behind its tip. A receipt from before a long-moved main is stale.
5. **Coverage** — every required part (`py-shard-0..3` by default) is present with
   `exit_code == 0`.

Does **not** prove:

- that the parts were actually executed honestly. A key holder can sign a receipt
  claiming green shards it never ran. The signature authenticates the *claimant*, not
  the *claim*. Honesty comes from **increment 6** (below), not from cryptography.
- anything about parts in `skipped` (browser-proofs, windows-shard). Hosted CI still owns
  those.
- that the merge result is green: the receipt covers the head tree, not the merge with
  main. Freshness bounds the gap; the main-full post-merge run closes it.

## Action behaviour (increment 3)

`.github/workflows/verify-receipt.yml`, `pull_request` on main, one ubuntu job:

| Receipt on head sha | Result |
|---|---|
| none | **succeeds** with a `::notice` + step summary "no receipt" — not required yet, never blocks a PR |
| present, valid | succeeds, prints `receipt VALID` |
| present, invalid (tree/head/base/signature/required part) | **fails** — a bad receipt is a real defect even while non-required |
| present, cannot evaluate (malformed, unknown scheme, no key material) | **fails** (exit 2, fail-closed) |

Exit codes of `tools/verify_receipt.py`: `0` valid, `1` invalid, `2` cannot evaluate.

**The "none" row is a lookup outcome, not just an evaluation outcome.** The fetch step
(`tools/verify_receipt.py --fetch-for-head`) only ever LOOKS for a receipt; a failure
while looking -- a `gh api` error, a malformed check-run payload, or the PR's own tree
predating this tool entirely (the job checks out the PR head, not main) -- is
indistinguishable from "no receipt was posted" and always degrades to the "none" row,
never to a crash. Only a receipt the fetch step actually extracted and then handed to
`verify()` can land on the "invalid" or "cannot evaluate" rows. (2026-10-06 incident:
PRs #856/#857 showed FAILURE because the inline fetch script raised
`ModuleNotFoundError` on branches cut before this file existed -- fixed by making the
lookup exception-safe and guarding the module's own absence at the shell level.)

## Increments to come

4. **Protection change** — add `verify-receipt` to required checks and make the python
   shards of `ci.yml` conditional on a *missing* receipt (hosted run only when no valid
   receipt exists). Needs the measurement data from this period: receipts vs hosted
   results on the same sha must agree.
5. **Gate-inventory registration** — register the receipt as a first-class gate in
   `tools/gate_inventory.py` / `verify_gates_wired.py` so "receipt replaces shard" is
   itself verified wiring, not prose.
6. **Random audit + main-full cross-check** — the honesty mechanism: a random fraction
   of receipt-accepted PRs still get a hosted re-run, and `main-full.yml` (post-merge)
   is compared against the receipt that admitted each merge. A disagreement revokes
   trust for that key (the key_id in the receipt) and reverts to hosted-only.

## Measurement period: what to look at

- For each PR with a receipt: did `verify-receipt` pass, and did `ci (0..3)` pass on the
  same head sha? Any disagreement is the signal that blocks increment 4.
- Receipt count vs PR count: adoption by lanes (`emit_receipt.py --post` in the lane
  contract is advisory for now).

## Proof runs

2026-10-07: push-emission proof
