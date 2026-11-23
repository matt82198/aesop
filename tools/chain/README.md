# Chained read commands

Run one command for a bounded JSON digest of a GitHub or local read. Copy
[config.example.json](config.example.json) to a private location and set its repository
names, local paths, PR number, run ID and byte offsets. Each command needs only its
own settings. Relative paths resolve from the current working directory. Use Node 20+
and install Git and an authenticated GitHub CLI for the commands that use them.

```sh
node tools/chain/pulls.mjs config.json
node tools/chain/pr.mjs config.json
node tools/chain/feeds.mjs config.json
node tools/chain/worktrees.mjs config.json
node tools/chain/failing-lines.mjs config.json
node tools/chain/board.mjs config.json
node --test tools/chain/chain.test.mjs
```

| Task | Returned facts | Detail available from the raw read |
| --- | --- | --- |
| T1 open PRs | Each row is `[repo, number, headSHA, mergeStateStatus, checkCount, nonSuccessChecks]`. The last field contains name/state pairs, including queued, neutral and skipped checks. A zero check count identifies an empty rollup. Successful empty repos appear in `emptyRepos`. | Individual successful checks, workflow identity, timestamps and check URLs |
| T2 one PR | State, merge commit SHA or null, all check name/state pairs, and up to three newest comment first lines in chronological order | Comment authors, URLs, times, remaining body text, older comments and check metadata |
| T3 feeds | Complete inbox lines and peer events, in source order. Peer rows retain all fields except the implied `kind: "peer"`. Each source returns its next byte offset and observed file size. | Original JSON serialization and line endings |
| T4 worktrees | Path, branch, upstream, ahead, behind and dirty entry count. A rename counts once, conflicts count once, and individual untracked files count. Detached heads are labeled. Null divergence means Git supplied no comparison. A bare repository has its own row. | HEAD OIDs, lock/prune annotations, changed paths and individual status codes |
| T5 failing lines | Up to 16 pairs of original one-based line number and log text, in log order. Named failures and Actions error annotations have first priority, then other error indicators, then other non-pass lines. A line containing a failure indicator takes precedence over a pass phrase. | Remaining matches, assertion diffs, stack frames and surrounding output |

**Reading the board.** T1, T3 and T4 start concurrently. `data.pulls.rows` contains
`[repo, openCount, flaggedCount, examples]`. Each example gives the PR number, merge
state and non-success check count. Up to three examples per repo have a non-clean
merge state or a non-success check. Counts describe the inspected input.
`data.feeds.rows` gives source, row count and a 100-character latest-message preview.
`data.worktrees.rows` gives branch, ahead, behind and dirty count for changed trees.
Each section has a size allowance. The board is a polling view. Use T3's `next`
offsets to acknowledge feed consumption.

**Bounds and failures.** Stdout contains one JSON object and a newline, at most
32,768 UTF-8 bytes per task or 1,920 for the combined board. The process status
matches `exitCode`. Any failed read exits 1 and populates `errors`; multi-source
tasks retain successful sources where they fit. The board names failed sections
and reports the first diagnostic and error count per section. Child commands use
`execFile` argument arrays, a 30-second default per-command timeout and a 64 MiB
buffer per stream. Authentication errors and command failures include the child
exit code and a bounded stderr excerpt. Timeouts are labeled. Missing executables
report their OS error. Git status uses optional locks disabled and local tracking
refs. T4 runs up to eight status reads at once.

`truncated` marks an output limit, a partial feed line, or board sampling. A
nonzero `exitCode` separately marks an unsuccessful read. Fixed field projections
are described in the table above. T1 probes for a 1,001st open PR and returns at
most 1,000 per repo before the global byte cap. T5 clips individual lines at 1,024
UTF-16 units. Oversized indivisible results return a bounded error object.

**Feed offsets.** Use zero or the byte immediately after a newline. Each reader
starts at that offset and reads up to 1 MiB, keeping up to 14,000 encoded bytes of
rows. Persist each successful source's `next` value and retry when `more` is true.
An unfinished final line waits for its newline. A cursor past EOF, an invalid
offset, malformed activity JSON or a row too large to page produces a source
error. A same-size or longer replacement file requires the caller to reset its
cursor after rotation.

## Repeating the comparison

For A, launch one process per raw read, retain its stdout and stderr, and keep the
same config for the digest reads. The argument lists in `github.mjs` and
`worktrees-lib.mjs` define the exact GitHub and Git queries.

| Task | A commands | B commands | C commands |
| --- | --- | --- | --- |
| T1 | One `gh pr list --state open --limit 1001 --json number,headRefOid,mergeStateStatus,statusCheckRollup` per configured repo with `--repo` | One T1 command | Shared board |
| T2 | One `gh pr view` for the configured number with `--repo` and `--json state,mergeCommit,statusCheckRollup,comments` | One T2 command | Outside board |
| T3 | Two byte-range reads from the configured offsets, emitting complete original lines through the last newline. For activity, parse each row and emit its original line only when `kind === "peer"`. | One T3 command | Shared board |
| T4 | One `git worktree list --porcelain -z`, then one `git status --porcelain=v2 --branch --ahead-behind --untracked-files=all -z` per non-bare worktree, with `--no-optional-locks -C <path>` | One T4 command | Shared board |
| T5 | One `gh run view <id> --repo <repo> --log` | One T5 command | Outside board |

Count external caller invocations, UTF-8 response bytes and decoded Unicode
characters divided by four. Keep the output wrapper and newline in the B/C totals.
Time caller round trips separately from the commands inside them. Rotate A/B/C
order on a live board, record errors and compare identified facts against the
retained A captures. For C, charge its single call once and report its section
sizes and concurrent elapsed times separately. Raw captures can contain private
messages and filesystem paths. Keep benchmark inputs and captures local.
