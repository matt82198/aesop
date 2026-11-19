# Shared results home

Every harness writes its results here, in one layout, so runs can be compared from evidence.

Layout: `results/<harness>/<run_id>/<task_id>.json`, one file per task per run. The shape is in `schema.json`.

Rules:

- Write rows only through `../record_result.py`. It runs the task's hidden check against `--workdir` and records `passed` and `check_exit` from that run. Harness claims are never trusted.
- Rows are never overwritten. Re-recording an existing row exits 2. Start a new `--run-id` instead.
- `null` means the harness did not report that number. Totals skip nulls and count them as `unreported_input_tokens`.
- A task with no row for a harness is `missing`. It is not a pass and not a fail.
- Read a run with `../compare_results.py --run-id <id>`.
