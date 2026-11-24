# Native ticket qualification

[RESULTS.md](RESULTS.md) compares the two measured native arms. Each ran three repetitions of the same three tickets. The score columns count passing ticket tests, edits confined to the requested file, and accepted edits. Acceptance also required successful inference and a normal finish within the deadline.

Regenerate the table from the committed per-trial receipts, offline, from the repository root.

```sh
python -m bench.native_headless.report --receipts bench/native_headless/receipts.json --out bench/native_headless/RESULTS.md
```

The receipt projection keeps the original scores and rollout hashes. The importer joined each scored slot to its saved rollout, verified its SHA-256, and read the driver and provider from session metadata and the model ID from turn context. To repeat that import on the archive host, supply `--archive <native-ticket-comparison-directory> --save-receipts <json> --out <markdown>`.

For a new shard, supply a factory checkout containing the frozen `evals/harness/rerun-03/run.json`. The native runner includes the ticket fixtures, grading helpers, and loopback gateway from factory revision `5b2086fe5c41d50f82ac1265f4420214004c2f98`. The installed Docker grader image, CLI version, fixture hash, and prompt hashes must match the receipt. For example, the local arm uses:

```sh
python -m bench.native_headless.qualify --kernel . --factory <factory-checkout> --out <new-shard-directory> --ticket windows --provider local_eval --model eval-local-30b-32k:v1
```

`local_eval` starts the factory TLS gateway for the selected local model. The hosted arm takes `--provider openai --model gpt-6.2-astra` and uses the CLI's existing login. Each shard writes one row per completed trial. A missing identity, conflicting rollout evidence, or a mismatch with the requested arm stops scoring.
