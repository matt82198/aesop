# Receipt format

One job: list every field, constant, flag and exit code of a signed receipt, so you can read or
produce one without opening the source. For the reasoning, see
[Signed receipts](../concepts/signed-receipts.md). For hands-on use, see
[Verify a receipt](../guides/verify-receipt.md).

Status: the receipt gate is in a measurement period and the hosted check is not required
(`docs/archive/RECEIPT-GATE.md:3-4`). Fields below are what the code on `origin/main` emits and accepts.

## Envelope

```json
{"receipt": { ... }, "sig": { ... }}
```

Both keys must be objects, otherwise the verifier exits 2 (`tools/verify_receipt.py:132-136`). The
emitter builds the envelope after signing (`tools/emit_receipt.py:789-791`).

## `receipt` fields

Emitted by `build_receipt` (`tools/emit_receipt.py:565-587`).

| Field | Type | Meaning | Verifier rule |
|---|---|---|---|
| `schema` | integer | envelope schema, always `1` | must equal `1`, else exit 2 (`tools/verify_receipt.py:137-138`) |
| `schema_version` | integer | forward-compatibility marker, `1` | absent is accepted as 1; any other value is an invalid-receipt reason (`tools/verify_receipt.py:209-211`) |
| `profile` | string | matrix selection: `scoped` or `full` | `full` keeps the caller's required list; anything else, or absent, has its shard requirement recomputed (`tools/verify_receipt.py:182-191`) |
| `repo` | string | `owner/name`, or `unknown` | not checked |
| `head_sha` | string | 40 hex, the commit the matrix ran on | must be 40 hex and equal the checkout head (`tools/verify_receipt.py:139-141`, `tools/verify_receipt.py:221-222`) |
| `base_sha` | string | 40 hex, merge-base of head and trunk | must be 40 hex, an ancestor of the trunk, and at most `--max-behind` commits behind (`tools/verify_receipt.py:228-236`) |
| `trunk_ref` | string | trunk ref used for the base, for example `origin/main` | not checked; the verifier resolves its own |
| `tree_hash` | string | 40 hex, `git rev-parse HEAD^{tree}` | must equal the value recomputed in the verifier's checkout (`tools/verify_receipt.py:223-225`) |
| `parts` | list | one object per part that ran | must be a list (`tools/verify_receipt.py:142-143`) |
| `skipped` | list | `{name, reason}` for parts left out | never required |
| `host` | object | `os`, `python`, `hostname_hash` (first 16 hex of SHA-256 of the host name) | not checked |
| `env` | object | `scrubbed`: names of environment variables hidden from the parts | not checked |
| `timestamp` | string | UTC, `YYYY-MM-DDTHH:MM:SSZ` | not checked |

Optional fields added only in some cases:

| Field | When | Meaning |
|---|---|---|
| `pushed_red` | emitter run with `--pushed-red` and a red conclusion | the emergency override was used; stamped before signing so it is covered by the signature (`tools/emit_receipt.py:785-788`) |
| `posted` | `--post` with an `origin` that is configured and not a github.com remote | `{skipped: "non-github-remote", remote: ...}`; records that no check-run was posted (`tools/emit_receipt.py:532-545`, `tools/emit_receipt.py:780-783`) |

### `parts[]` items

Each part is `{name, exit_code, test_count, duration_s}` (`tools/emit_receipt.py:338-339`). The verifier
reads only `name` and `exit_code` (`tools/verify_receipt.py:239-248`). `test_count` is an integer or
`null` when no count could be parsed (`tools/emit_receipt.py:216-221`).

Part names in the default registry are `py-shard-0` to `py-shard-3`, `secret-scan`,
`claudemd-sync-gate`, `gen-tool-index`, `verify-test-suite-count`, `encoding-lint`,
`import-resolution-check`, `sibling-import-check` and `linux-shape`; `browser-proofs` and
`windows-shard` are registered as not runnable on a dev box and appear under `skipped`
(`tools/emit_receipt.py:61-78`). A part that times out records exit code 124
(`tools/emit_receipt.py:271`).

<!-- count: 14 from python -c "import sys; sys.path.insert(0,'tools'); import emit_receipt as e; print(len(e.DEFAULT_REGISTRY))" on origin/main 8ede5e06 -->

## `sig` fields

| Field | Type | Meaning |
|---|---|---|
| `scheme` | string | `ed25519` or `hmac-sha256` (`tools/receipt_common.py:31`) |
| `value` | string | base64 of the signature bytes (`tools/receipt_common.py:97`, `tools/receipt_common.py:104`) |
| `key_id` | string | 16 hex characters; for Ed25519 the SHA-256 of the public PEM (CRLF normalized), for HMAC the SHA-256 of the secret (`tools/receipt_common.py:78-81`, `tools/receipt_common.py:105`) |

The signature covers the canonical bytes of the `receipt` object only: sorted keys, separators `,`
and `:` with no spaces, ASCII-escaped (`tools/receipt_common.py:40-42`,
`tools/emit_receipt.py:788`). This is not RFC 8785.

## Where a receipt is stored

It is not a committed file (`tools/emit_receipt.py:3`). It is posted on the head sha under the name
`verify-receipt-local` (`tools/receipt_common.py:24`):

1. As a check-run, with the envelope in `output.text`.
2. If the token cannot create check-runs (HTTP 403), as a commit comment.
3. If the sha does not exist on GitHub yet (HTTP 422), spooled to `<head_sha>.json` in the spool
   directory and flushed later (`tools/emit_receipt.py:630-678`).

The text stored in channels 1 and 2 is the marker line `<!-- aesop-receipt:verify-receipt-local -->`,
then the envelope JSON inside a fenced `json` block (`tools/receipt_common.py:25`,
`tools/emit_receipt.py:620-622`, `tools/verify_receipt.py:28`). The verifier extracts the newest
matching check-run or comment (`tools/verify_receipt.py:266-290`).

Spool directory resolution, highest priority first: `$AESOP_RECEIPT_SPOOL`; the git common directory's
`aesop-receipt-spool`; `$HOME/.aesop/receipt-spool` (`tools/receipt_common.py:188-198`).

## Verifier exit codes

`tools/verify_receipt.py`:

| Exit | Meaning | Cite |
|---|---|---|
| 0 | valid | `tools/verify_receipt.py:196`, `tools/verify_receipt.py:249` |
| 1 | invalid, or no receipt found | `tools/verify_receipt.py:249`, `tools/verify_receipt.py:437-441` |
| 2 | cannot evaluate: unreadable or malformed envelope, no key material for the scheme, git failure, empty `--required` | `tools/verify_receipt.py:250-251`, `tools/verify_receipt.py:200-204` |

Lookup mode (`--fetch-for-head`) exits 0 when a receipt was found and 1 when none was
(`tools/verify_receipt.py:397-418`).

## Verifier flags

From `build_parser` (`tools/verify_receipt.py:363-394`):

| Flag | Default | Meaning |
|---|---|---|
| `--receipt` | none | envelope file, or `-` for stdin |
| `--repo` | `.` | checkout to verify against |
| `--head` | `HEAD` | commit to verify |
| `--main-ref` | resolved like the emitter's trunk | ref for the freshness check; an explicit value that does not resolve is refused |
| `--max-behind` | `50` | most commits the base may trail the trunk |
| `--required` | `py-shard-0,py-shard-1,py-shard-2,py-shard-3` | comma-separated part names |
| `--pubkey` | `<repo>/tools/receipt_pubkey.pub` | Ed25519 public key |
| `--keyring` | `<pubkey dir>/receipt_pubkeys` | extra registered keys directory |
| `--allow-no-required-parts` | off | accept an empty `--required` |
| `--extract` | none | `check-runs` or `comments`: treat `--receipt` as a GitHub API payload |
| `--fetch-for-head SHA` | none | look up a receipt via `gh api` instead of verifying |
| `--fetch-window-s` | `300` | poll seconds, `0` to `900` |
| `--fetch-interval-s` | `20` | seconds between lookups, at least `1` |
| `--repo-slug` | none | `owner/repo`, required with `--fetch-for-head` |
| `--out-found`, `--out-envelope` | `receipt_found`, `receipt.json` | lookup output files |

The `--required` list is applied as given for a `full` receipt. For a scoped receipt the shard names
are recomputed from the files changed between `base_sha` and `head_sha`, and any underivable case
falls back to all four shards (`tools/verify_receipt.py:161-191`).

## Emitter exit codes

`tools/emit_receipt.py` (`tools/emit_receipt.py:604-616`, `tools/emit_receipt.py:824`):

| Exit | Meaning |
|---|---|
| 0 | receipt produced and green, or red without `--fail-on-red` |
| 2 | could not produce or publish: no key material, unknown part, both publish channels failed |
| 3 | red and `--fail-on-red`; the pre-push hook blocks on it |
| 4 | red with `--pushed-red`; the receipt is stamped `pushed_red: true` |

Check-run conclusion: `failure` when any part is red or there are no parts, `neutral` when every part
passed but no python shard ran, `success` otherwise (`tools/emit_receipt.py:590-601`).

## Emitter flags

From `python tools/emit_receipt.py --help`: `--repo`, `--matrix`, `--jobs`, `--scheme
{auto,ed25519,hmac-sha256}`, `--key`, `--slug`, `--main-ref`, `--out`, `--post`, `--dry-run`,
`--fail-on-red`, `--pushed-red`. `--dry-run` performs no network call even with `--post`.

## Environment variables and constants

| Name | Role | Cite |
|---|---|---|
| `AESOP_RECEIPT_KEY` | Ed25519 private key PEM path | `tools/receipt_common.py:28` |
| `AESOP_RECEIPT_HMAC_SECRET` | HMAC secret, on both signer and verifier | `tools/receipt_common.py:29` |
| `AESOP_RECEIPT_SPOOL` | spool directory override | `tools/receipt_common.py:30` |
| `AESOP_RECEIPT_PROFILE` | `scoped` (default) or `full` | `tools/emit_receipt.py:89-91` |
| `AESOP_PUSH_RED` | emergency override, hook-side; emitter exit 4 | `docs/archive/RECEIPT-GATE.md:30-31` |
| `AESOP_RECEIPT_EMIT` | inert; logged and ignored | `docs/archive/RECEIPT-GATE.md:32-33` |
| `CHECK_NAME` | `verify-receipt-local` | `tools/receipt_common.py:24` |
| `DEFAULT_PUBKEY_REL` | `tools/receipt_pubkey.pub` | `tools/receipt_common.py:26` |
| `DEFAULT_REQUIRED_PARTS` | the four python shards | `tools/receipt_common.py:27` |
| `DEFAULT_KEY_REL` | `.aesop/receipt_key.pem`, relative to HOME or `AESOP_HOME` | `tools/receipt_common.py:32` |

## Limits

- A valid receipt does not prove the parts ran honestly (`tools/verify_receipt.py:5-9`).
- The hosted check is not required (`docs/archive/RECEIPT-GATE.md:3-4`); this reference does not claim
  receipts replace any CI run.
- Fields marked "not checked" are informational. Only the rules in the table are enforced.
