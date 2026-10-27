# Verify a receipt

One job: check a signed receipt offline, with no network, and read the verdict. You need Python 3 and
a git checkout of the commit the receipt claims to describe. Ed25519 receipts also need the
`cryptography` package; HMAC receipts need nothing beyond the standard library
(`tools/receipt_common.py:10-13`).

Background first, if you want it: [The receipts loop](../concepts/receipts-loop.md) and
[Signed receipts](../concepts/signed-receipts.md).

All outputs below come from real runs in a clean checkout of `origin/main` at commit `8ede5e06`.

## 1. Make a receipt to practice on

Skip this step if you already have an envelope file (for example one a lane posted). To practice, emit
one locally. `--dry-run` signs and prints but performs no network call (`tools/emit_receipt.py:3`).
The secret below is a throwaway demo value, not a credential.

```
$ export AESOP_RECEIPT_HMAC_SECRET=doc-demo-not-a-secret
$ python tools/emit_receipt.py --matrix secret-scan --scheme hmac-sha256 --dry-run --out demo-receipt.json
part secret-scan                  exit=0 tests=None 12.5s
scrub env for parts: AESOP_RECEIPT_HMAC_SECRET, AESOP_RECEIPT_KEY
conclusion=neutral head=8ede5e06091e tree=a88c51071284 scheme=hmac-sha256
{
 "receipt": {
  "base_sha": "8ede5e06091ed80d8b57122e60e1d2eaa43088d9",
  "env": {
   "scrubbed": [
...
dry-run: nothing posted
```

With `--dry-run` the emitter also prints the whole signed envelope to stdout (cut to its first lines
above, marked `...`); `--out` writes the same JSON to the file. The `neutral` conclusion is expected
here: every part passed but no python shard ran (`tools/emit_receipt.py:590-601`). The first 8 lines
of the file, `head -n 8 demo-receipt.json`, literal output:

```
{
 "receipt": {
  "base_sha": "8ede5e06091ed80d8b57122e60e1d2eaa43088d9",
  "env": {
   "scrubbed": [
    "AESOP_RECEIPT_HMAC_SECRET",
    "AESOP_RECEIPT_KEY"
   ]
```

Keys are sorted, so `base_sha` comes first. To see the schema, profile, tree and parts, read them
back with a command (literal output):

```
$ python -I -c "import json; d=json.load(open('demo-receipt.json')); r=d['receipt']; print(r['schema'], r['profile'], r['tree_hash']); print([(p['name'], p['exit_code']) for p in r['parts']]); print(d['sig']['scheme'], d['sig']['key_id'])"
1 scoped a88c510712843b37f239c3b4e685bffaca75dc80
[('secret-scan', 0)]
hmac-sha256 566b675b066ca973
```

`tree_hash` is the tree of `HEAD`. You can see the same value from git:

```
$ git rev-parse 'HEAD^{tree}'
a88c510712843b37f239c3b4e685bffaca75dc80
```

## 2. Verify it

Name the parts you require. The default list is `py-shard-0` to `py-shard-3`
(`tools/receipt_common.py:27`); this practice receipt ran only `secret-scan`, so say so.

```
$ python tools/verify_receipt.py --receipt demo-receipt.json --required secret-scan
receipt VALID (head=8ede5e06091e scheme=hmac-sha256 parts=1)
$ echo $?
0
```

The verifier recomputed the tree from your checkout, checked the head, the base freshness, the
signature and the required parts (`tools/verify_receipt.py:194-249`). To check a receipt from a
different commit, check that commit out first or pass `--head <sha>`
(`tools/verify_receipt.py:366-367`).

## 3. Read the three exit codes

| Exit | Label printed | Meaning | Cite |
|---|---|---|---|
| 0 | `VALID` | every check passed | `tools/verify_receipt.py:249`, `tools/verify_receipt.py:462` |
| 1 | `INVALID`, or `MISSING receipt` | a check failed, or no receipt was found | `tools/verify_receipt.py:249`, `tools/verify_receipt.py:437-441` |
| 2 | `CANNOT EVALUATE` | the envelope is unreadable, or there is no key material for its scheme | `tools/verify_receipt.py:250-251`, `tools/verify_receipt.py:214-216` |

Each failing check adds one line under the verdict. Real examples follow, each from the receipt
above with one thing changed.

**A part claims a failure** (the file was edited after signing, so the signature breaks too):

```
$ python tools/verify_receipt.py --receipt t1.json --required secret-scan
receipt INVALID (head=8ede5e06091e scheme=hmac-sha256 parts=1)
  - signature: does not verify under hmac-sha256
  - required part secret-scan exit_code=1
$ echo $?
1
```

**The tree hash was swapped** for a different one:

```
$ python tools/verify_receipt.py --receipt t2.json --required secret-scan
receipt INVALID (head=8ede5e06091e scheme=hmac-sha256 parts=1)
  - signature: does not verify under hmac-sha256
  - tree_hash: receipt b88c51071284 != recomputed a88c51071284
$ echo $?
1
```

**The wrong secret** on the verifying side:

```
$ AESOP_RECEIPT_HMAC_SECRET=wrong python tools/verify_receipt.py --receipt demo-receipt.json --required secret-scan
receipt INVALID (head=8ede5e06091e scheme=hmac-sha256 parts=1)
  - signature: does not verify under hmac-sha256
$ echo $?
1
```

**No secret at all**, so the verifier cannot judge:

```
$ env -u AESOP_RECEIPT_HMAC_SECRET python tools/verify_receipt.py --receipt demo-receipt.json --required secret-scan
receipt CANNOT EVALUATE (head=8ede5e06091e scheme=hmac-sha256 parts=1)
  - no key material to verify scheme hmac-sha256 (pubkey=.../tools/receipt_pubkey.pub, hmac=unset)
$ echo $?
2
```

**No receipt file** is exit 1, not a pass (the line continues with the head sha, author and a hint):

```
$ python tools/verify_receipt.py --receipt none.json --required secret-scan
MISSING receipt: no receipt file none.json for head 8ede5e06091ed80d8b57122e...
$ echo $?
1
```

An empty `--required` list is refused with exit 2 unless you pass `--allow-no-required-parts`, which
is meant only for a deliberate local check; CI never passes it (`tools/verify_receipt.py:200-204`).

## 4. Verify an Ed25519 receipt

Ed25519 needs the `cryptography` package. Generate a throwaway pair, sign, and verify with the public
half. The key generator is a function in `tools/receipt_common.py`, not a command
(`tools/receipt_common.py:58-75`). These outputs are from one real run, executed in the repository
root with the files in the current directory. The emit step was run as
`env -u AESOP_RECEIPT_HMAC_SECRET python tools/emit_receipt.py ... | head -n 3`, so the signed JSON
the emitter also prints is not shown; the unset secret keeps the HMAC path out of the run.

```
$ python -c "import sys; sys.path.insert(0,'tools'); import receipt_common as rc; rc.generate_ed25519_keypair('priv.pem','demo.pub'); print('key_id', rc.key_id_for_pubkey(open('demo.pub','rb').read()))"
key_id 27095c6de2ca833e
$ python tools/emit_receipt.py --matrix secret-scan --scheme ed25519 --key priv.pem --dry-run --out ed.json
part secret-scan                  exit=0 tests=None 12.4s
scrub env for parts: AESOP_RECEIPT_KEY
conclusion=neutral head=8ede5e06091e tree=a88c51071284 scheme=ed25519
$ python tools/verify_receipt.py --receipt ed.json --pubkey demo.pub --required secret-scan
receipt VALID (head=8ede5e06091e scheme=ed25519 parts=1)
```

Now verify the same file against the repository's own registered keys, which do not include your
throwaway key:

```
$ python tools/verify_receipt.py --receipt ed.json --required secret-scan
receipt INVALID (head=8ede5e06091e scheme=ed25519 parts=1)
  - signature: does not verify under ed25519
$ echo $?
1
```

That is the keyring working as designed. A key counts only once its public half is registered in
`tools/receipt_pubkey.pub` or `tools/receipt_pubkeys/` and merged
(`docs/archive/RECEIPT-GATE.md:156-175`). Never commit a private key (`docs/archive/RECEIPT-GATE.md:161`).

## 5. Verify a receipt posted on a PR

Receipts are posted on the head sha as a `verify-receipt-local` check-run or a commit comment
(`docs/archive/RECEIPT-GATE.md:95-102`). To look one up, `verify_receipt.py` has a lookup mode that uses the
`gh` CLI and writes the envelope to a file, then you verify that file as in step 2:

```
python tools/verify_receipt.py --fetch-for-head <sha> --repo-slug <owner>/<repo> --fetch-window-s 0
python tools/verify_receipt.py --receipt receipt.json --repo . --head <sha>
```

This step needs network access and an authenticated `gh`; it was not run for this page, so no output
is shown. Lookup mode exits 0 when it found a receipt and 1 when none was found
(`tools/verify_receipt.py:397-418`). `--fetch-window-s 0` makes it a single lookup instead of the
default 300 second poll (`tools/verify_receipt.py:34-35`, `tools/verify_receipt.py:385-387`).

## What a pass means

A VALID verdict means the key holder signed these parts on a tree matching your checkout, the parts
exited 0, and the base is within 50 commits of the trunk. It does not mean the parts ran honestly or
that the merge with main is green (`tools/verify_receipt.py:5-9`, `docs/archive/RECEIPT-GATE.md:192-201`).
The hosted check is also not required today (`docs/archive/RECEIPT-GATE.md:3-4`).

## Check the verifier itself

```
$ python -m unittest tests.test_verify_receipt
Ran 48 tests in 25.647s

OK
```

<!-- count: 48 from `python -m unittest tests.test_verify_receipt` on origin/main 8ede5e06 -->

The same run prints a few fixture diagnostic lines after the summary; they are test output, not
failures.

See also: [Receipt format](../reference/receipt-format.md).
