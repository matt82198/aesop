# Receipt signing-key isolation: design options

Status: design brief (0.10.1-prep item S1). No code changes in this document.
Scope: `tools/emit_receipt.py`, `tools/receipt_common.py`, `tools/verify_receipt.py`,
`hooks/pre-push-policy.sh` (receipt section), `.github/workflows/verify-receipt.yml`,
`docs/RECEIPT-GATE.md`. Every claim about current behaviour cites file:line at
the base commit `bbe698c5`.

Notation: "lane" = any aesop dispatched agent or session process running as the
same OS user as the box owner. The brief assumes lanes run as that user; this was
not checked against the process table (see Open questions).

## 1. Summary

A lane can forge a receipt today. The signing key lives at a path that any
same-user process can compute and open, and the only check before signing is that
the file exists. Forgery does not change any merge gate today because the hosted
`verify-receipt` check is non-required during the measurement period
(`docs/RECEIPT-GATE.md:3-4`). The severity is therefore High for the integrity of
the receipt record and Low for merge gating at present. It becomes High as soon as
a receipt is made required.

Recommendation (detail in section 7): option C (GitHub-issued in-toto attestation
from the CI runner) is the only option that closes the forgery threat on this box
without admin rights. Option A cannot close it against same-user processes on
Windows without a second principal. Option B is viable only for the CI-runnable
subset and is subsumed by C in practice.

## 2. Current state (verified, with citations)

### 2.1 Where the key is, and who can open it

- The key env names are `AESOP_RECEIPT_KEY` and `AESOP_RECEIPT_HMAC_SECRET`
  (`tools/receipt_common.py:28-29`).
- The default key file is `$HOME/.aesop/receipt_key.pem`, or
  `$AESOP_HOME/.aesop/receipt_key.pem` (`tools/receipt_common.py:32`,
  `tools/receipt_common.py:283-288`). Resolution falls back to `HOME` with no
  check on who is calling.
- The pre-push hook gates signing on file existence only:
  `hooks/pre-push-policy.sh:1934-1937` tests `[ ! -f "${AESOP_HOME:-$HOME}/.aesop/receipt_key.pem" ]`.
  It never checks permissions or owner.
- Key generation sets `os.chmod(private_path, 0o600)` inside a `try` that swallows
  `OSError` (`tools/receipt_common.py:70-74`, comment at line 73 says "Windows
  ACLs"). On Windows, Python's `chmod` only toggles the read-only attribute; it does
  not set an ACL, so this is not a read restriction for the owning user.
- No code in `tools/`, `hooks/` or `daemons/` sets an ACL (`icacls`, `SetNamedSecurityInfo`)
  or a socket/pipe for signing. A search for `icacls|chmod|NamedPipe|AF_UNIX` in those
  roots returns only the `chmod` calls listed above and unrelated `chmod +x` uses.

### 2.2 The matrix that runs next to the key

- `emit_receipt.py` runs the matrix as child processes in a throwaway worktree.
  `run_part` copies the parent environment (`tools/emit_receipt.py:256`), sets
  `TMPDIR` (`:259`), and runs `subprocess.run(argv, cwd=throwaway_wt, env=env)`
  (`:260-261`).
- The parts are `ci_shard_runner.py` (py-shard-0..3) and the fast gates, all taken
  from the pushed head (`tools/emit_receipt.py:53-55` builds argv from `tools/...`
  under the repo root; registry at `:57-71`). A part therefore executes the pushed
  head's code, which is the code under review.
- The scrub removes only names in `SCRUBBED_ENV_VARS` (`tools/emit_receipt.py:216-228`,
  `scrub_child_env` at `:230-239`). The list includes `AESOP_RECEIPT_KEY` and
  `AESOP_RECEIPT_HMAC_SECRET` (`:222`). It does NOT include `HOME`, `USERPROFILE`,
  `AESOP_HOME`, or any path that the file fallback reads.
- The comment at `tools/emit_receipt.py:219-221` says an inherited HMAC secret "(or key
  path)" would let part code forge a receipt, and that "matrix parts never" read the
  signing material. The code removes the env names only. The file path is still
  reachable from a part, because `HOME` is inherited. This is a documented control
  that does not exist as written.

### 2.3 Where the signature is made and checked

- Scheme choice prefers Ed25519 when a key path resolves (`tools/emit_receipt.py:529-538`).
- The key path is resolved again in `main` (`tools/emit_receipt.py:568`), and the
  signature is made at `tools/emit_receipt.py:620`
  (`rc.sign(rc.canonical_json(receipt), scheme, private_key_path=key_path, hmac_secret=secret)`).
- `sign()` loads the PEM with `load_pem_private_key` from the path it is given and
  signs the canonical bytes (`tools/receipt_common.py:84-98`).
- `verify_signature()` uses the PUBLIC key only for Ed25519 (`tools/receipt_common.py:109-137`).
  The verifier holds no secret for Ed25519. The HMAC scheme is symmetric and holds the
  secret on both sides (`docs/RECEIPT-GATE.md:146-152`; code at `tools/receipt_common.py:118-125`).
- The verifier trusts the public key and keyring from the BASE tree, not the PR head
  (`.github/workflows/verify-receipt.yml:30-33`, `:142-145`). A lane cannot make its own
  key verify on the hosted check by committing a key on its branch.
- The hosted check is not required (`docs/RECEIPT-GATE.md:3-4`). The local receipt is
  the only artifact a lane produces today.

### 2.4 What the receipt proves, per the project's own doc

`docs/RECEIPT-GATE.md:190-194` says a key holder can sign a receipt that claims green
shards it never ran, and that the signature "authenticates the claimant, not the claim".
This brief agrees. Option A closes the signer-identity part of that gap for receipts
the oracle itself produces, but not for claims the oracle does not run itself.

### 2.5 Corrections to prior notes

The brief and memory notes were checked against the code. These three corrections are
not repeated as fact anywhere in this doc:

1. **No hash chain over receipts.** A search of the receipt code
   (`tools/emit_receipt.py`, `tools/verify_receipt.py`, `tools/receipt_common.py`,
   `tools/receipt_flush.py`, `tools/receipt_autoflush.py`, `tools/receipt_agreement.py`)
   for `chain`, `prev`, `parent` returns nothing relevant. The only hash chain in the
   repo is the audit log (`hooks/pre-push-policy.sh:444`, `docs/HOOK-INSTALL.md:113`).
   A receipt has no link to a prior receipt, so deleting or reordering receipts is not
   detected by any check.
2. **Canonicalization is JCS-like, not RFC 8785.** `canonical_json` is
   `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True)`
   (`tools/receipt_common.py:40-42`). That matches JCS for strings and integers but
   not for all numbers. The signed receipt contains floats: `duration_s` is
   `round(..., 3)` (`tools/emit_receipt.py:267`). Python writes `1.0` where RFC 8785
   writes `1`. A non-Python verifier that implements JCS would reject valid receipts.
3. **Per-box keys are supported in code.** `tools/receipt_common.py:143-176` implements a
   keyring of extra Ed25519 public keys, and `docs/RECEIPT-GATE.md:154-159` documents
   provisioning. The memory note that per-box keys were "deferred 2026-10-14" describes a
   process decision. It does not match the code, and this doc does not assume the
   deferral.

## 3. Threat model used for the comparison

- T1. A lane (same OS user) reads the signing key and produces receipts for any tree,
  head, or parts result.
- T2. A lane produces a receipt claiming parts it never ran (the RECEIPT-GATE honesty gap).
- T3. A lane alters a stored receipt or the local spool after signing.
- T4. A lane alters the verifier, the keyring, or the workflow that checks receipts.

Current status: T1 open (2.1, 2.2). T2 open (2.4). T3 open (no chain, 2.5 item 1).
T4 mitigated for the hosted check by base-tree trust (`verify-receipt.yml:30-33`) and
open for local checks, which read the lane's own tree.

## 4. Option A: local signing oracle

### 4.1 Design

A separate process holds the private key and is the only process that opens the key
file. Lanes send requests over a local IPC channel. The oracle does not sign a digest
the lane supplies. It recomputes the tree, head and base itself (as
`verify_receipt.py:164-165` already does), runs the matrix itself through `run_matrix`
(`tools/emit_receipt.py:343`) in its own throwaway worktree, builds the receipt, and
only then signs it with `rc.sign` (`tools/receipt_common.py:84`). It appends the signed
envelope to a store that lanes can read but not write.

### 4.2 Threat closed

- T1 is closed against lanes that do not know how to reach the key file: lanes never
  receive the key path or bytes, and `emit_receipt.py` no longer calls
  `resolve_receipt_key_path` (`:568`) or `rc.sign` (`:620`).
- T2 is closed for receipts the oracle produces: the oracle runs the parts itself, so
  a lane cannot request a green receipt for parts that did not run.

### 4.3 Threat left open

- T1 against same-user processes. On Windows the owner of a file always has the
  implicit right to change its DACL (WRITE_DAC is granted to the owner). A same-user
  lane can rewrite the ACL on the key file and then read it. This is standard Windows
  owner behaviour; it was not reproduced on this box in this brief. An ACL therefore
  protects against accidental reads, not a deliberate same-user process.
- Pipe or socket abuse. Any same-user process can connect to the oracle and request
  signatures. The oracle's only protection is what it chooses to sign. If it signs
  whatever it is asked, T2 returns. The oracle must run the matrix itself, which means
  a lane can only get a receipt for a tree the oracle can build and test.
- Killing or replacing the oracle process, or planting a fake oracle on the same pipe
  name. Pipe name squatting must be considered; the oracle must verify the peer or the
  IPC must be authenticated, and a same-user peer check does not stop a same-user
  attacker.
- T3 and T4 are not changed by option A. The hosted check still uses base-tree trust.

### 4.4 Changes to tools/

- New: `tools/receipt_oracle.py` (listener, key holder, runs `run_matrix`, signs, appends).
- `tools/emit_receipt.py`: `main` (`:560-646`) becomes a client. Remove the key
  resolution at `:568` and the signature at `:620` for lanes; keep a `--local-unsigned`
  path that is labelled as unsigned.
- `tools/receipt_common.py`: `resolve_receipt_key_path` (`:264-288`) must not be used by
  any lane process; a unit test must prove that a lane-side call returns no key path.
  The `HOME` fallback at `:283-288` is the file-based path that T1 uses.
- `tools/emit_receipt.py:222`: extend the scrub or move the key outside `HOME`; the
  scrub alone does not close the file path (2.2).
- `tools/verify_receipt.py`: no change to the signature check (`:127-131`); the
  verifier still needs only the public key.
- `hooks/pre-push-policy.sh:1934-1937`: the presence check must change to "oracle
  reachable", not "file exists".

### 4.5 Windows feasibility on this box (no WSL, no admin)

- Process and IPC: feasible with the stdlib. CPython `socket.AF_UNIX` is reported as
  available on Windows 10 1803 and later; the box is Windows 11 Pro. This was not tested
  here. Named pipes need `pywin32` or ctypes, which conflicts with the stdlib-only rule
  in `tools/CLAUDE.md` (cited in `tools/receipt_common.py:10-12`).
- Key storage: Ed25519 via `cryptography` (optional import, `tools/receipt_common.py:45-52`).
  Without admin the oracle cannot run under a different OS account, so the key is
  always reachable by a same-user process that tries hard enough.
- Hardware-bound option (TPM / Microsoft Platform Crypto Provider with a user-presence
  prompt): this could make each signature need a user action, which a lane cannot click.
  It was not evaluated on this box (TPM state and CNG provider were not inspected) and is
  noted only as a direction to check.

### 4.6 Size

About 6 Haiku lanes: (1) oracle process and IPC transport with a Windows transport
spike, (2) oracle run-matrix-and-sign path, (3) emitter client and removal of lane-side
key resolution, (4) hook presence check rewritten to oracle reachability, (5) tests that
drive the real path and prove the lane-side call cannot reach the key (anti-vacuity:
break the lane-side removal, show red, restore, show green), (6) docs and migration.

## 5. Option B: keyless signing (Sigstore, OIDC identity)

### 5.1 Design

The signer obtains a short-lived certificate bound to an OIDC identity and signs with
an ephemeral key. Nothing long-lived is stored. In practice the identity must be a
GitHub Actions workflow identity, because that is the only identity a lane cannot
mint on demand. A local signer would need an interactive browser login as Matt, which
is a human identity, not a per-lane identity, and is rejected below.

Actions workflows already have `id-token: write` in this repo (`.github/workflows/publish.yml:9`,
used for npm OIDC at `:70`), so the runner side is not new.

### 5.2 Which receipts can move to CI, and which cannot

Can move (CI-runnable, already in CI):

- `py-shard-0..3`: `ci.yml` runs `python-shard: [0, 1, 2, 3]` (`.github/workflows/ci.yml:157-158`),
  which matches the receipt registry (`tools/emit_receipt.py:58-61`).
- `linux-shape` (`tools/emit_receipt.py:69`, marked `requires_wsl=True`): on a Linux runner
  it does not need WSL. The receipt skips it locally on a box without WSL.
- The fast gates in `_FAST_PARTS` (`tools/emit_receipt.py:77-79`): secret-scan,
  claudemd-sync-gate, gen-tool-index, verify-test-suite-count, encoding-lint,
  import-resolution-check, sibling-import-check. Whether each has a `ci.yml` job
  equivalent was not mapped in this brief; treat as "probably, to be verified".
- `browser-proofs` and `windows-shard` are already hosted and skipped by the receipt
  (`tools/emit_receipt.py:70-71`).

Cannot move (must stay local or lose their gate effect):

- The pre-push conclusion gate. The hook blocks a push when the matrix is red
  (`hooks/pre-push-policy.sh:1860-1870`, decision at `:3316-3318`). A CI signature
  arrives after the push, so it can block a merge but not the push. Any check that
  must stop a push has to run before the push, locally.
- Pre-push policy checks in `hooks/pre-push-policy.sh` itself (branch policy, the
  generated-path and secret policy). These run before the push and are not in the
  receipt registry.
- Local-only facts: `host.hostname_hash` (`tools/emit_receipt.py:440`) and the local
  `scrubbed` list (`:442`) describe the lane's box. They have no meaning in CI and
  should not be in a public attestation.

### 5.3 Threat closed

- T1 is closed for CI-produced receipts: no key exists to read. A lane's local box
  cannot obtain a certificate for the workflow's identity.
- T3 is partly closed: the transparency log entry (Rekor) makes deletion of a published
  attestation detectable, if the verifier checks inclusion.

### 5.4 Threat left open

- The identity is "this workflow in this repo", not "this trusted runner for main". A
  PR that edits the workflow file can run its own workflow and produce a valid
  attestation for its own claims. The mitigation is to pin the signer workflow and its
  ref in the verifier (for example `gh attestation verify --signer-workflow` and a
  ref policy), with the policy read from the base tree.
- T2 remains for the code under test: the CI matrix runs the PR's own test code. A PR
  that makes its tests pass attests to green. The attestation proves the workflow ran
  on that commit, not that the tests are meaningful. This is the same class of gap as
  RECEIPT-GATE 190-194.
- Rekor is a public log. An attestation exposes the repo, workflow, subject digest and
  timestamps. Confirm the repo's visibility and that no local identifiers are included.

### 5.5 Changes to tools/

- `emit_receipt.py`: no keyless path for lanes. Local signing is unchanged and labelled
  advisory.
- `receipt_common.py`: `sign`/`verify_signature` gain a keyless branch only if the
  verifier is allowed a non-stdlib dependency. `tools/CLAUDE.md` requires stdlib-only
  (`receipt_common.py:10-12`). The practical route is to keep the verifier stdlib and
  call `gh attestation verify` from the workflow.
- `verify_receipt.py`: `_signature_ok` (`:127-131`) gains a branch that accepts an
  attestation reference and checks it with `gh`, or the workflow runs `gh` and passes a
  verdict.
- `.github/workflows/verify-receipt.yml`: the signer identity and ref policy are pinned
  from the base tree (as the key is now, `:30-33`).

### 5.6 Windows feasibility on this box

- The signer runs on CI, so the box needs no Windows-specific code. No admin, no WSL.
- Local interactive OIDC (browser login) would make Matt's identity sign each receipt.
  Feasible on Windows, but rejected: it is not per-lane, and it requires a human step per
  push.
- `gh` is already a dependency of the hook (`hooks/pre-push-policy.sh:1929`).

### 5.7 Size

About 4 Haiku lanes: (1) CI job that runs the CI-runnable subset and attests, (2) verifier
branch that calls `gh attestation verify` with pinned signer workflow, (3) base-tree ref
policy and its tests (break the pin, show red, restore, show green), (4) docs. Note the
CI-runnable subset is smaller than the local matrix, so receipts for the local-only
checks remain local and advisory.

## 6. Option C: in-toto attestations from the CI runner

### 6.1 Design

A CI job runs the matrix on the pushed head and emits an in-toto v1 Statement whose
subject is the head's tree hash (or the head sha, with the tree as a field), and whose
predicate records the parts and their exit codes. The statement is attested with the
GitHub-issued identity of that job (artifact attestation, Sigstore-signed, with the
workflow identity in the certificate). The verifier checks the attestation against the
repo and signer workflow, then recomputes the tree itself as it does now
(`verify_receipt.py:164-165`).

The predicate type is a new custom type that has to be defined. Do not reuse a
standard predicate for it.

### 6.2 Threat closed

- T1 closed for CI-produced attestations: the key is GitHub's, not the box's.
- T3 mostly closed: the subject is a digest, so a changed tree breaks the binding
  cryptographically rather than via a field check, and the attestation is stored with
  GitHub.
- T4 for the hosted path: the verifier and signer policy come from the base tree.

### 6.3 Threat left open

- T2 for the code under test, as in B (5.4). The attestation says the matrix ran on the
  commit in that workflow; it does not say the tests are good.
- Workflow edits in the PR (5.4). Mitigated by signer-workflow pinning in the verifier.
- Local-only checks (5.2) are not covered and must stay advisory.
- Local receipts still exist and can still be forged. They must be labelled as
  unsigned-by-trusted-party in the receipt schema, or they remain a misleading signal.

### 6.4 Changes to tools/

- `.github/workflows/`: a new job (non-required during measurement) that runs
  `ci_shard_runner.py n 4` for n in 0..3 and writes the statement, then attests it.
- `tools/verify_receipt.py`: a `--attestation` mode that checks the attestation against
  the signer workflow pinned from the base tree and compares the subject digest to the
  recomputed tree (`:164-165`).
- `tools/emit_receipt.py`: unchanged for lanes. Keep local signing as advisory and mark
  the schema field.
- `tools/receipt_common.py`: predicate schema constant only.

### 6.5 Windows feasibility on this box

- Feasible with no local secrets. The box needs `gh` (already a hook dependency,
  `hooks/pre-push-policy.sh:1929`) and network access for the verifier. No admin, no WSL.
- Whether artifact attestations are available depends on the repo's visibility and plan.
  Repo visibility was not checked in this brief. This is the first thing to confirm.

### 6.6 Size

About 3 Haiku lanes: (1) attest job and statement writer, (2) verifier `--attestation`
mode with tests driven through the real verifier entry point, (3) docs and the
local-advisory labelling.

## 7. Recommendation and first PR

Recommendation: option C, with B's identity rules applied to it. Keep local Ed25519
receipts as advisory and stop describing them as proof. Do not try to fix option A's
same-user gap on this box, because the stdlib-only rule and the lack of admin leave no
principal that a lane cannot read as.

Two cheap fixes are independent of the choice and only reduce the gap; neither is a
control on its own: (a) correct the scrub comment at `tools/emit_receipt.py:219-221`
so it matches the code, and (b) make the hook's key check at
`hooks/pre-push-policy.sh:1934-1937` state that it is a presence check, not an
access control.

First PR (implementation, after this design is accepted): a non-required CI job that
runs the four py-shards on the pushed head, writes an in-toto statement with a new
predicate type, attests it, and a `verify_receipt.py --attestation` mode that is driven
through its real entry point. No change to required checks (`docs/RECEIPT-GATE.md:3-4`).
Test proof: break the signer-workflow pin in the verifier, show red, restore, show
green. About 3 Haiku lanes.

## 8. Open questions

1. Whether lanes run as the same OS account as the box owner. Assumed, not checked.
2. Repository visibility and plan, which decide whether artifact attestations are
   available (6.5).
3. Whether `socket.AF_UNIX` works on this box for option A (4.5). Not tested.
4. Whether the TPM / CNG user-presence path is available on this box (4.5). Not checked.
5. Mapping of the fast gates to `ci.yml` jobs (5.2). Not done.
6. Whether a JCS-conformant canonicalization is wanted (2.5 item 2). Changing it
   invalidates existing receipts and needs a schema version bump
   (`tools/verify_receipt.py:149-150` already checks `schema_version`).
