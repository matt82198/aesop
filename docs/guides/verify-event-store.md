# Verify an event store

This guide checks an event store's hash chain. You will also see what a broken chain looks like.
Nothing needs installing. Every command uses the Python standard library and runs from the
repository root.

Background is in two concept pages. [Event store](../concepts/event-store.md) explains the chain.
[Per-project stores](../concepts/per-project-stores.md) explains the registry.

Use `sh` syntax. Git Bash works, and so does any POSIX shell. Replace `<work>` with an empty
directory outside any git repository. The expected output comes from running these exact
commands on `origin/main` at commit `8ede5e06`.

## What "verify" means here

`verify` recomputes the `prev` value of every event. It also checks the sequence numbers. Each
problem gets one output line, and the command exits `1` when there is any problem
(`tools/event_store/cli.py:100-119`, `tools/event_store/store.py:113-145`). It does not check the
full key list or the timestamps. Nothing outside the store anchors the chain either. The
[limits](../concepts/event-store.md#limits) section names that gap.

## Step 1: Run the unit tests

```sh
python -m unittest tests.test_event_store_core
```

Expected last lines. The timing figure differs by run.

```text
Ran 15 tests in 0.973s

OK
```

This suite covers the chain, the CLI refusals and the v1 read-only paths. It is
`tests/test_event_store_core.py`, with `StoreChainTests`, `CliTests` and `V1NoStoreJsonTests`.

The guard script is not in this suite. `tests/test_event_store_init.py` tests it.

If the suite does not print `OK`, stop here.

## Step 2: Create a throwaway store

Create the store directory and a registry that names it. Both files go under `<work>`.

`<work>/stores/demo/store.json`:

```json
{"project": "demo", "schema": 2}
```

`<work>/registry.json`:

```json
{"stores": {"demo": {"path": "<work>/stores/demo", "goals": ["G9"]}}}
```

Write the registry's `path` as an absolute path. Next, create the `events` directory and append
two events. `append` creates the day file itself.

```sh
# Make the events directory, then point the CLI at the registry you just wrote.
mkdir -p <work>/stores/demo/events
export EVENT_STORES_REGISTRY=<work>/registry.json
# First event: a note that carries the goal reference, so the registry routes it to demo.
python -I tools/event_store/cli.py append \
  --project demo \
  --kind note \
  --actor orchestrator \
  --text "first demo event" \
  --ref goal=G9

# Second event: a ruling, which the CLI refuses unless it carries a goal reference.
python -I tools/event_store/cli.py append \
  --project demo \
  --kind ruling \
  --actor orchestrator \
  --text "second demo event" \
  --ref goal=G9
```

Expected output:

```text
appended demo seq=1
appended demo seq=2
```

A `ruling` needs a `goal` reference (`tools/event_store/cli.py:79-80`). A `--ref` key that
encodes a repository is refused. An example is `demo_pr_1` (`tools/event_store/cli.py:20`,
`:34-36`).

## Step 3: Verify the chain

```sh
python -I tools/event_store/cli.py verify --path <work>/stores/demo
echo "exit=$?"

python -I tools/event_store/cli.py verify --project demo
echo "exit=$?"
```

Expected output, both times:

```text
verify: 0 problem(s)
exit=0
```

Each day file holds one JSON object per line, with keys sorted. The second line's `prev` is the
SHA-256 of the first line's bytes.

```text
{"actor":"orchestrator","kind":"note","prev":"","refs":{"goal":"G9"},"seq":1,"session":"","text":"first demo event","ts":"<timestamp>","v":2}
{"actor":"orchestrator","kind":"ruling","prev":"<64-hex sha256 of line 1>","refs":{"goal":"G9"},"seq":2,"session":"","text":"second demo event","ts":"<timestamp>","v":2}
```

The timestamp and hash change between runs. The first `prev` is always `""`
(`tools/event_store/store.py:106`).

The repository's committed fixture `tests/fixtures/event_store/` holds only `source-v1.jsonl`,
an input for the migration tests (`tests/test_event_store_migrate.py:19`). It is not a store, so
`verify` refuses it:

```sh
python -I tools/event_store/cli.py verify --path tests/fixtures/event_store
echo "exit=$?"
```

Expected output (the path separator follows your OS):

```text
refused: cannot read tests/fixtures/event_store/store.json: [Errno 2] No such file or directory: 'tests/fixtures/event_store/store.json'
exit=2
```

`2` means refused, not "chain broken" (`tools/event_store/cli.py:116-117`). The test suite builds
its stores in temporary directories, for example `make_store` in `tests/test_event_store_core.py:25`.

## Step 4: See a broken chain

Copy the store and edit one event in the copy. Leave the original alone.

```sh
rm -rf <work>/tampered && mkdir -p <work>/tampered
cp -r <work>/stores/demo/. <work>/tampered/
sed -i 's/first demo event/EDITED demo event/' <work>/tampered/events/*.jsonl
python -I tools/event_store/cli.py verify --path <work>/tampered
echo "exit=$?"
```

Expected output:

```text
<work>/tampered: <day>.jsonl:2: prev mismatch (chain broken or earlier line removed)
verify: 1 problem(s)
exit=1
```

The problem appears on line 2. Line 2's `prev` no longer matches the edited line 1
(`tools/event_store/store.py:130-133`).

Editing the last line goes unreported. The same copy test, with `second demo event` changed to
`EDITED last`, printed `verify: 0 problem(s)` and exited `0`. No later line stores the last
line's hash, so nothing checks it. Only an outside record of that hash would reveal the change
from the store alone. In a git-backed store, the guard refuses the commit that makes the change,
but only in a worktree where the guard is installed.

## Step 5: Check the append-only guard

The guard is a pre-commit script. It refuses a commit that deletes, renames or rewrites an
existing line in `events/*.jsonl` (`tools/event_store/guard/events_append_only.sh:2-4`,
`:23-32`). Test it in a scratch repository. Replace `<aesop>` with your aesop checkout path.

```sh
git init -q <work>/guardrepo
mkdir -p <work>/guardrepo/events
printf '{"seq":1}\n{"seq":2}\n' > <work>/guardrepo/events/2026-10-27.jsonl
cd <work>/guardrepo && git add events && git -c user.email=t@t -c user.name=t commit -qm base

printf '{"seq":1}\n{"seq":3}\n' > events/2026-10-27.jsonl
git add events
sh <aesop>/tools/event_store/guard/events_append_only.sh
echo "exit=$?"
```

Expected output. Windows may print `LF will be replaced by CRLF` warnings first.

```text
events-append-only: REJECTED. events/*.jsonl is append-only. Staged changes that delete or rewrite existing lines:
  1 line(s) removed or rewritten in events/2026-10-27.jsonl
Fix: unstage and restore the file, then record the new fact with python -I tools/event_store/cli.py append.
exit=1
```

A pure append passes.

```sh
printf '{"seq":1}\n{"seq":2}\n{"seq":3}\n' > events/2026-10-27.jsonl
git add events
sh <aesop>/tools/event_store/guard/events_append_only.sh
echo "exit=$?"
```

Expected output:

```text
exit=0
```

The guard also refuses when git cannot report the staged diff, with exit `1`
(`events_append_only.sh:12-20`).

## Troubleshooting

| Message | Cause | Source |
|---|---|---|
| `refused: cannot read registry <path>` | `EVENT_STORES_REGISTRY` points at a missing or invalid file, or the default `~/.claude/event-stores.json` is absent | `tools/event_store/registry.py:22-27` |
| `refused: registry <path> has no 'stores' object` | The JSON has no `stores` object | `registry.py:28-29` |
| `refused: project <name> is not in the registry` | No entry under that name, or the entry has no `path` | `registry.py:44-47` |
| `refused: store ... has no store.json (v1 log, read-only)` | The directory has day files and no `store.json`, so it opens as read-only schema 1 | `tools/event_store/store.py:39-45`, `:92-94` |
| `refused: ts ... is older than latest day file` | The `ts` day is before the newest day file | `store.py:101-102` |
| `refused: store <name> at <path> belongs to '<other>', not '<name>'` | Single-writer check: the `store.json` project differs from the name used | `tools/event_store/cli.py:86-88` |
| `git commit failed` on `append` | The store is its own worktree and `git add` or `git commit` failed. The event line is already written. | `tools/event_store/cli.py:53-58` |
| `CHAIN BROKEN <name>: ...` in `project` output | That store's chain failed verification. The view skips the store. | `tools/event_store/project.py:49-51` |

`verify` returns `0` when clean and `1` when it finds problems. It returns `2` for a refusal,
such as an unreadable registry (`tools/event_store/cli.py:116-117`).

## Clean up

Delete `<work>` when you finish. The steps write nothing to the repository and make no commit
or push. `append` commits only when the store directory is its own
git worktree. Nothing here creates one (`tools/event_store/cli.py:46-47`).
