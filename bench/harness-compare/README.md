# Harness-neutral comparison corpus

A fixed set of coding tasks that any harness can run identically, so Antigravity (agy) and Claude Code with aesop machinery can be compared from recorded evidence. This directory holds the corpus, the scripts that prepare and check it, and the shared results home. It makes no model or API calls.

Why not `bench/`: `bench/` is a string-match scorer. A checker corpus needs real seed copies and hidden check scripts, so it lives here.

## Run loop

Each task is run once per harness. Five lines per task:

1. `python prepare_task.py --task <id> --dest <empty-dir>` gives a fresh copy of the seed.
2. The harness works in that directory, given only `corpus/tasks/<id>/prompt.md`.
3. `python check_task.py --task <id> --workdir <dir>` is an optional local diagnostic. The recorded verdict comes from step 4.
4. `python record_result.py --harness <h> --model <m> --run-id <r> --task <id> --workdir <dir> --wall-seconds <s> --started-at <iso-with-tz> [--input-tokens N --output-tokens N --cost-usd X --turns N --tool-calls N] [--notes ...]`
5. `python compare_results.py --run-id <r>` prints the matrix, missing counts, and disagreements.

## Rules that keep the comparison fair

- Identical prompt: each harness gets the exact text of `prompt.md`, with no harness, model, or tool names added. The check, not the harness, decides pass or fail.
- Fresh copy: every run starts from `prepare_task.py`, never from a directory a previous run touched. `prepare_task.py` refuses a non-empty destination.
- Hidden checks: each check lives at `corpus/tasks/<id>/reference/check.py`, beside the reference module, so its plain imports resolve statically. `run_check` copies it into the working directory as `_hidden_check.py` for one run and removes it afterwards. `overlay_reference` never copies it. The seed holds no checker files.
- Reference proof: every seed fails its check and every `reference/` passes it. `tests/test_harness_compare_corpus.py` enforces both.
- Missing is missing: a task with no row for a harness is shown as `missing`, never as a pass or a fail.

## Corpus

`corpus/manifest.jsonl` has 12 rows, one per task. Each task directory holds `prompt.md`, `seed/` and `reference/`. The hidden check is `reference/check.py`.

| Category | Task ids |
| --- | --- |
| bug-fix | bug-paginate, bug-slugify, bug-merge-intervals |
| feature-test-first | feat-retry-backoff, feat-csv-quoted |
| refactor | refactor-dedupe-validators, refactor-split-report |
| multi-file | multi-rename-across-package, multi-config-env-override |
| read-answer | read-changelog-version |
| niche-nuance | nuance-leap-day-age, nuance-money-round-half-even |

## Files

- `harness_corpus.py` shared helpers (manifest, prepare, check, JSON I/O, name validation).
- `prepare_task.py`, `check_task.py`, `record_result.py`, `compare_results.py` CLIs.
- `results/` shared results home. See `results/README.md` and `results/schema.json`.
- Tests: `tests/test_harness_compare_corpus.py` and `tests/test_harness_compare_results.py`.

Run the tests (stdlib, offline):

`python -m unittest discover -s C:/Users/Mattt/aesop-wt-harness-corpus/tests -p test_harness_compare_corpus.py` and the same with `test_harness_compare_results.py`.
