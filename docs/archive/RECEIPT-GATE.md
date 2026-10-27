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
| 2a | Spool-and-flush: spool receipts if commit missing, flush when sha exists | `tools/receipt_flush.py` |
| 2b | Offline verifier | `tools/verify_receipt.py` |
| 3 | Hosted verifying Action (non-required) | `.github/workflows/verify-receipt.yml` |

**Emission is now automatic.** `hooks/pre-push-policy.sh`'s `check_emit_receipt()` runs
`tools/emit_receipt.py --post` on every push, once every gate above it has passed (see
hooks/CLAUDE.md item 16). A day-1 measurement of the manual-only design found 41 merged
PRs and ZERO receipts — lanes were never told to run the emitter and nothing ran it for
them, so the honesty signal had no data. The hook is a CONCLUSION GATE: a red required
part (emitter exit 3 under `--fail-on-red`) or an unreadable conclusion (timeout, any other
emitter exit) blocks the push, and the failure receipt is still posted as the trace; a
missing python, `gh`, signing key, or base emitter BLOCKS too (fail closed: no conclusion can
be produced or signed), and a skipped-with-reason part never blocks. `AESOP_PUSH_RED=1` is the
one recorded emergency override (emitter exit 4; the receipt is stamped `pushed_red: true`).
`AESOP_RECEIPT_EMIT=0` is INERT: it is logged as `receipt_emit_optout_ignored` and emission
still runs, because no orchestrator-only mechanism exists to honor it. Manual invocation still
works the same way, e.g. for a `--dry-run` preview or a narrower `--matrix`:

```
python tools/emit_receipt.py --post            # default matrix, Ed25519 if $AESOP_RECEIPT_KEY is set
python tools/emit_receipt.py --dry-run         # print the signed envelope, post nothing
python tools/emit_receipt.py --matrix py-shard-0,py-shard-1 --post
```

## Isolation mechanism

Matrix parts run in a **detached throwaway worktree** at the HEAD commit, not the caller's
tree, so tests that create fixture git repos and commit cannot mutate the lane's branch.
Parts inherit a scrubbed environment (GIT_DIR, GIT_WORK_TREE, and related vars unset) as
defense in depth — git's hook environment would otherwise leak into parts and let them
commit to the lane via inherited GIT_DIR. The caller tree is verified untouched via
tripwire (HEAD, index tree hash, working tree status compared before and after). The spool
directory (state/receipts/spool) lives outside the throwaway worktree so spooled receipts
survive its removal.

## Spool-and-flush design (increment 2a)

**Problem:** The pre-push hook runs before the commit is pushed to GitHub, so when
`emit_receipt.py --post` tries to publish, the commit-comment fallback fails with 422
("No commit found for SHA"). The receipt is lost.

**Solution:** Spool the receipt to `state/receipts/spool/<head_sha>.json` when 422 occurs,
then flush it once the sha exists on GitHub. The flush runs automatically:

1. **At the start of `check_emit_receipt`** in `hooks/pre-push-policy.sh`: Before emitting
   a new receipt, `tools/receipt_flush.py` checks any prior spooled receipts and posts them
   if their shas now exist on GitHub. The flush runs with scrubbed environment (GIT_* vars
   unset) to maintain isolation. Fail-open: errors do not block the push.

2. **Detached poller (`tools/receipt_autoflush.py --spawn`)**: when the hook's emit reports
   "spooled", it forks a background poller that checks `gh api repos/<slug>/commits/<sha>` every
   15s for up to 10 minutes and posts that sha's receipt the moment it exists. The push never
   waits on it. Its outcome (posted / gave up with the reason) goes to `<spool>/<sha>.flush.log`.
   `AESOP_RECEIPT_AUTOFLUSH=0` disables it.

3. **Watchdog backstop (`daemons/run-watchdog.sh` `run_receipt_flush`)**: every cycle, on both the
   `--once` and daemon paths, runs `receipt_flush.py --repo $AESOP_ROOT`. `tools/pr_sweep.py` also
   flushes, but for its own repo. It used to target the hardcoded `~/aesop`, a stale non-git copy,
   so the spool (which lives in the pushing repo's git common dir) was never found and nothing was
   posted. That was the recurring gap: a spooled receipt sat until someone re-ran the flush by hand.

4. **verify-receipt poll window**: `verify-receipt.yml` polls for the receipt for up to 300s (every
   20s, `--fetch-window-s` / `--fetch-interval-s` on `verify_receipt.py --fetch-for-head`), stopping as
   soon as it appears. The autoflush posts only after the push lands, so a single immediate lookup lost
   the race on fast CI starts (PR #1035). It stays fail-closed on MISSING, and the job summary
   names the attempt that found it.

The flush tool is idempotent: it retries failed receipts indefinitely (up to `--max-age-days`,
default 7 days), deletes them on success, and keeps them on transient errors (sha not yet pushed).
Exit 0 always unless `--strict`.

```
python tools/receipt_flush.py --repo .              # flush spooled receipts
python tools/receipt_flush.py --repo . --dry-run    # print the plan
python tools/receipt_flush.py --repo . --max-age-days 3  # drop stools older than 3 days
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
   "schema": 1, "schema_version": 1, "profile": "scoped",
   "repo": "owner/name",
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
the base) invalidates it. The optional `schema_version` field (default: absent, treated as version 1 for backward compatibility) enables forward-compatible extensions without breaking existing verifiers. The optional `profile` field (default: absent, treated as "scoped" for backward compatibility) records which profile was used to select the matrix.

## Matrix profiles: default scoped, full available

By default, the pre-push receipt emits using `profile: "scoped"`: it runs the fast pre-push gates always (`secret-scan`, `claudemd-sync-gate`, `gen-tool-index`, `verify-test-suite-count`, `encoding-lint`, `import-resolution-check`, `sibling-import-check`) PLUS only the python shards (`py-shard-0..3`) that own files changed in this push (derived from `git diff --name-only <base>..<tip>` mapped through the shard assignment in `ci_shard_runner.py`'s round-robin distribution). Shards not in scope are not recorded in the receipt today: `tools/emit_receipt.py` builds their skip reasons (`skipped_infos`, around line 607) but never writes them into `skipped`.

The scoped profile is the default because the full matrix (`py-shard-0..3`) can exceed pre-push timeout budgets on slow boxes (measured ~1250s locally while the hook timeout is 900s); scoped runs reduce that to minutes when most changes are docs-only or touch a few test files. Parts that cannot run on a dev box (`browser-proofs`, `windows-shard`) remain always-skipped. The verifier does not trust a scoped receipt's shard list: for a receipt with profile `scoped` (or absent, for legacy receipts), `tools/verify_receipt.py` RECOMPUTES the required shards from the receipt's own changed files (`git diff --name-only base_sha..head_sha`, mapped through `emit_receipt._find_owning_shards` over the checkout's shard map), and the receipt is VALID only if every required shard is present with exit 0. Fail-closed rule: when the owning set is unknown (`_find_owning_shards` returns None, the shard map or the change set cannot be derived, or an unowned `tools/` change) or empty (a docs-only change, where an empty scope would pass with zero Python tests run), ALL four shards are required. A `full`-profile receipt keeps the caller's `--required` list.

- **Scoped (default):** `AESOP_RECEIPT_PROFILE=scoped` or unset. Run fast parts + owning shards only.
- **Full:** `AESOP_RECEIPT_PROFILE=full`. Run fast parts + all shards (today's behavior). Available for explicit measurement or CI runs; set on the command line when you want the old behavior.
- **Manual override:** `python tools/emit_receipt.py --matrix py-shard-0,secret-scan --post` always respects an explicit `--matrix` argument regardless of profile.
- **Underivable mapping:** If the shard assignment cannot be determined (e.g., `git ls-files` fails), the profile falls back to `full` to stay safe.

Default matrix: `py-shard-0..3` (`tools/ci_shard_runner.py n 4`) plus the pre-push gate
set, with scoping applied per the profile above. Parts that cannot run on a dev box
(`browser-proofs`, `windows-shard`) are recorded under `skipped` with a reason —
never silently dropped — and are *not* in the verifier's required list.

## Signing scheme

**Ed25519** (`cryptography`, optional import) is the scheme in use: the private key
lives at `$AESOP_RECEIPT_KEY` on the signing box and is never in the repo; if unset,
the emitter falls back to `~/.aesop/receipt_key.pem` (or `$AESOP_HOME/.aesop/receipt_key.pem`
if `AESOP_HOME` is set), allowing shells that don't inherit the env var to still find the key.
The public key is committed at `tools/receipt_pubkey.pub` (ssh `.pub` naming because
`tools/secret_scan.py` rejects every `*.pem` filename as credential-shaped, which is the
right default for a repo). Asymmetric matters: the verifier holds **no secret**, so a
leaked Actions config cannot forge receipts.

**HMAC-SHA256** (stdlib) is the fallback for a box without `cryptography`, keyed by
`$AESOP_RECEIPT_HMAC_SECRET` on both the signer and the Action (`secrets.
AESOP_RECEIPT_HMAC_SECRET`). It is symmetric — whoever can verify can also sign — so it
is strictly weaker; `tools/` must stay stdlib-only (tools/CLAUDE.md), which is why it
exists. A receipt names its scheme; the verifier fails closed on an unknown one.

Rotation: `receipt_common.generate_ed25519_keypair(priv, pub)`, commit the new `.pub`.

**Keyring (per-box keys).** The verifier accepts several registered Ed25519 public keys: the
committed `tools/receipt_pubkey.pub` plus every `tools/receipt_pubkeys/*.pub`. A receipt is
checked against the registered key whose key_id (`sha256(pem)[:16]`) equals `sig.key_id`; the
signature must still verify under that key. Provisioning a new box: generate its private key
to `~/.aesop/receipt_key.pem` (outside any checkout) with `generate_ed25519_keypair`, then
commit its public key as `tools/receipt_pubkeys/<name>-<key_id>.pub`. Never commit a private key.

**Key rotation (base trust).** The verifier and keyring the Action uses come from the BASE tree
(`base.sha`, or `main` on `workflow_dispatch`), never from the PR head: a PR that registers a
signer key in its own checkout cannot make a receipt signed by that key verify (adversarial
finding on #970). So adding or rotating a key is a two-step flow:

1. Land the key in its own PR (`tools/receipt_pubkeys/<name>-<key_id>.pub`, or the rotated
   `tools/receipt_pubkey.pub`) and merge it to `main`.
2. Only after that merge do receipts signed by the new key verify. Until then such a receipt is
   `INVALID` ("signature: does not verify under ed25519") on every PR, including the key's own PR.

`verify_receipt.py --keyring DIR` names the registered-keys directory explicitly (the Action passes
`base/tools/receipt_pubkeys`). An empty `--required` list is refused (exit 2) unless the deliberate
local-only flag `--allow-no-required-parts` is given; CI never passes it.

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
| none | **fails** (exit 1, fail-closed: `MISSING receipt` names the sha and where it was looked for). Still non-required, so it does not block merges until promotion |
| present, valid | succeeds, prints `receipt VALID` |
| present, invalid (tree/head/base/signature/required part) | **fails** — a bad receipt is a real defect even while non-required |
| present, cannot evaluate (malformed, unknown scheme, no key material) | **fails** (exit 2, fail-closed) |

Exit codes of `tools/verify_receipt.py`: `0` valid, `1` invalid, `2` cannot evaluate.

**The "none" row is a lookup outcome, not just an evaluation outcome.** The fetch step
(`tools/verify_receipt.py --fetch-for-head`) only ever LOOKS for a receipt; a failure
while looking -- a `gh api` error, a malformed check-run payload, or the PR's own tree
predating this tool entirely (the job checks out the PR head, not main) -- is
indistinguishable from "no receipt was posted" and lands on the "none" row (fails
closed, exit 1), never on a crash. Only a receipt the fetch step actually extracted and then handed to
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
# Hook test
# Another test
# Test 4 after unset hooks path

2026-10-14: verified on the Mattt box - pre-push hook resynced from hooks/pre-push-policy.sh and a signed receipt emitted on push.

## Fail-closed contract (2026-10-21)

- Receipt emission failure blocks: missing python, `gh`, signing key material, base emitter, or a timeout blocks the push with a `BLOCKED:` line and a `push_blocked` audit event.
- `AESOP_RECEIPT_EMIT=0` is inert (logged as `receipt_emit_optout_ignored`); emission still runs.
- Exit 2 from the pyflakes ratchet or the CLAUDE.md headroom gate blocks, printing the tool output tail. Headroom gets one base-ref fetch or unshallow and one re-run first.
- Lock contention never drops a record: a busy audit lock spills to `state/audit-spill-<pid>-<ts>.jsonl`, merged by the next lock holder.
- Windows without WSL: a push touching Linux-shape covered paths blocks; uncovered pushes keep the NOTICE.
