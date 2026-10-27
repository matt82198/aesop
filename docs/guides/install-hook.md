# Install the pre-push hook

Goal: get `hooks/pre-push-policy.sh` running on every `git push` in your repository, register the Claude Code
hooks that ship beside it, and know which parts of enforcement your repository will not have until you set them
on GitHub. Background on what each layer does is in [Gates and hooks](../concepts/gates-and-hooks.md); the list of
checks is in [Gate reference](../reference/gates.md).

## Read this first: what a fork does not get

> **Required checks and branch protection are repository settings, not files.** This repository declares the
> checks it requires in `tools/required-checks.json`, but that file is a record of intent. A fork or a fresh
> copy starts with no branch protection and no required checks. Until you configure them on GitHub, nothing on
> the server stops a push that skipped the local hook (`git push --no-verify`), and nothing stops a merge with
> red CI. Section 4 below covers it.

The local hook is a convenience defense, not cryptographic protection (`docs/archive/HOOK-INSTALL.md:28`).

## 1. Install the git hook

### Option A: the scaffold installs it

Scaffolding a project, or running `init` in an existing repository, installs the hook for you:

```bash
npx @matt82198/aesop my-fleet
```

What the installer does, from `bin/cli.js`:

- It copies `hooks/pre-push-policy.sh` to `.git/hooks/pre-push`. It never symlinks, so clearing the npx cache
  cannot leave a dangling link that would silently disable the secret gate (`bin/cli.js:1536-1542`).
- If the existing hook is byte-identical it does nothing (`bin/cli.js:1519-1522`).
- If a different hook is already there it warns and stops, unless you pass `--force` (`bin/cli.js:1526-1532`;
  help text at `bin/cli.js:568`).
- If `.git/hooks/pre-push` is a symlink it refuses to write through it and tells you to remove it
  (`bin/cli.js:1508-1510`).
- In a git worktree it resolves the shared hooks directory through `git rev-parse --git-common-dir`
  (`bin/cli.js:449-466`).

### Option B: copy it yourself

```bash
cp hooks/pre-push-policy.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

The project's hook notes also describe a symlink on Unix (`hooks/CLAUDE.md:54`). Prefer the copy: a symlink into a
checkout you later move or delete turns the hook off without any message. If you copy, re-copy after you update
`hooks/pre-push-policy.sh`.

### Generate the files the hook expects

On a fresh repository the hook blocks until five generated files exist, because it judges pushes against them:
`tools/INDEX.md`, `.pyflakes-baseline.json`, `.subprocess-guard-baseline.json`, `.portability-baseline.json` and
`.stateapi-baseline.json` (`bin/cli.js:1226-1232`). The scaffold generates them from the scaffolded tree and
prints a warning naming the regenerate command for any it could not build (`bin/cli.js:1240-1242`). If you
copied the hook by hand, run the five commands listed in `bin/cli.js:1227-1231`, for example
`python tools/gen_tool_index.py --regenerate`, and commit the results.

### Test it

```bash
bash hooks/pre-push-policy.sh --test
```

Output on this tree, last lines, from a real run (the full run prints a PASS line per case):

```
=== Test Results ===
PASSED: 32
FAILED: 0
```

The run ends green.
<!-- bash hooks/pre-push-policy.sh --test -> PASSED: 32, FAILED: 0 -->

Running the hook directly in a terminal is refused on purpose. A real `git push` pipes the ref list to the hook on
stdin, so a hook that sees a terminal means a person ran it by hand, and it exits 1 with
`interactive invocation: this hook runs under git push` (`hooks/pre-push-policy.sh:3844-3849`). Only `--test` and
`--verify-audit-log` are meant to be run by hand (`hooks/pre-push-policy.sh:3831,3836`).

## 2. What your first push needs

The last gate in the chain is the receipt gate. It has preconditions that a new adopter usually does not meet on
day one. Most failures block the push. Two cases, a non-github.com origin and an origin with no branches yet,
allow the push without a receipt; they are marked in the table below (`hooks/pre-push-policy.sh:2363-2465`):

| Situation | Result | Audit reason |
|---|---|---|
| `origin` is not on github.com | Receipt not emitted, push allowed (no channel to post a conclusion) | `receipt_emit_non_github_origin` (`:2432`) |
| No python interpreter | Blocked | `receipt_emit_no_python` (`:2441`) |
| `gh` CLI missing or not working | Blocked | `receipt_emit_no_gh` (`:2447`) |
| No signing key material | Blocked | `receipt_emit_no_key_material` (`:2454`) |
| `origin` has no branches yet | Receipt not emitted, push allowed (bootstrap) | `receipt_emit_empty_remote_bootstrap` (`:2463`) |
| The test matrix comes back red | Blocked | `receipt_red_blocked` (`:2574`) |
| The matrix times out or its result cannot be read | Blocked | `receipt_emit_timed_out`, `receipt_emit_unreadable_blocked` (`:2553,2584`) |

Signing key material is any one of `AESOP_RECEIPT_KEY`, `AESOP_RECEIPT_HMAC_SECRET`, or a key file at
`${AESOP_HOME:-$HOME}/.aesop/receipt_key.pem` (`hooks/pre-push-policy.sh:2452`). The matrix defaults to a 900 second
bound, adjustable with `AESOP_RECEIPT_TIMEOUT` (`hooks/pre-push-policy.sh:2473`).

There is one recorded emergency override: `AESOP_PUSH_RED=1` lets a red head through, loudly, and the receipt is
stamped `pushed_red: true` (`hooks/pre-push-policy.sh:2478,2557-2565`). Setting `AESOP_RECEIPT_EMIT=0` does nothing
except log the attempt (`hooks/pre-push-policy.sh:2397`). Use of an override is itself counted: the escape-rate
counter raises an alert when more than a threshold of skip, opt-out and override events land in one hour
(`hooks/pre-push-policy.sh:1913-1916,1966`).

## 3. Register the Claude Code hooks

The three files under `hooks/claude/` are not active until a Claude Code `settings.json` points at them. This
repository registers exactly one, the model policy (`.claude/settings.json:3-14`; `docs/MACHINERY-MAP.md:288`). To
register all three, add to `.claude/settings.json` in your project, or to your user-level settings for every project:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [{ "type": "command", "command": "node \"$CLAUDE_PROJECT_DIR/hooks/claude/force-model-policy.mjs\"" }]
      },
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "node \"$CLAUDE_PROJECT_DIR/hooks/claude/no-heredoc-file-authoring.mjs\"" }]
      },
      {
        "matcher": "Bash|Read|Grep|Glob",
        "hooks": [{ "type": "command", "command": "node \"$CLAUDE_PROJECT_DIR/hooks/claude/no-scrollback-as-record.mjs\"" }]
      }
    ]
  }
}
```

The first block is the registration the repository itself uses; matchers for the other two come from
`hooks/CLAUDE.md:101,119-120`. If the hook files live outside the project, use an absolute path to each `.mjs`.

Behavior you should know before you turn them on:

- `force-model-policy.mjs` rewrites a non-compliant subagent `model` to `haiku`, or to
  `cardinal_rules.subagent_model` from `aesop.config.json` when you set it (`hooks/claude/force-model-policy.mjs:9,75-82`).
  Put `[[ALLOW-NON-HAIKU]]` in a prompt to pass a dispatch through untouched; the bypass is announced in the
  transcript and appended to `state/MODEL-POLICY-ESCAPES.log` (`hooks/claude/force-model-policy.mjs:10-14,39`).
- `no-heredoc-file-authoring.mjs` denies any heredoc in a Bash command. The escape token is
  `[[ALLOW-HEREDOC-WRITE]]` (`hooks/claude/no-heredoc-file-authoring.mjs:4,32,39`).
- `no-scrollback-as-record.mjs` denies reading `.claude/projects/**/*.jsonl` session transcripts as a record. The
  escape token is `[[ALLOW-TRANSCRIPT-READ]]` (`hooks/claude/no-scrollback-as-record.mjs:27,39,46`).
- Whether Claude Code actually fires a registered hook depends on your client version. The project's README says
  hooks are "currently not fully firing" (`README.md:100`). After registering, trigger each one deliberately and
  confirm you see the denial or rewrite.

Run the shipped tests for the three files:

```bash
node --import ./tests/helpers/isolated-env.mjs --test tests/force-model-policy.test.mjs
node hooks/claude/no-heredoc-file-authoring.test.mjs
node hooks/claude/no-scrollback-as-record.test.mjs
```

On this tree all three runs end green with no failures; the case counts are in the comments below.
<!-- node --import ./tests/helpers/isolated-env.mjs --test tests/force-model-policy.test.mjs -> tests 13, pass 13, fail 0 -->
<!-- node hooks/claude/no-heredoc-file-authoring.test.mjs -> 19 passed, 0 failed, 19 total -->
<!-- node hooks/claude/no-scrollback-as-record.test.mjs -> 34 passed, 0 failed, 34 total -->

## 4. Turn on server-side enforcement

Do this on GitHub, in the repository that owns your code. It cannot be done by committing a file.

1. Settings, Branches, add a branch protection rule for your default branch (`docs/archive/HOOK-INSTALL.md:58-62`).
2. Require a pull request before merging, require status checks to pass, and require branches to be up to date
   (`docs/archive/HOOK-INSTALL.md:64-69`).
3. Dismiss stale approvals when new commits are pushed (`docs/archive/HOOK-INSTALL.md:71-73`).
4. For "required status checks", choose the contexts your own CI produces. This repository's list is seven
   contexts: `ci (0)`, `ci (1)`, `ci (2)`, `ci (3)`, `windows`, `pr-footprint-gate`, `pr-symbol-survival`
   (`tools/required-checks.json:6-12`). <!-- node -e "console.log(require('./tools/required-checks.json').required_contexts.length)" -> 7 -->
   Those names are job names from this repository's `.github/workflows/ci.yml`. A required context with no job
   behind it can never report, so copy the names only if you also copy the jobs.
5. Keep `tools/required-checks.json` honest. `tools/required_checks_gate.py` reads live protection through the
   `gh` CLI and fails when the declared set and the enforced set differ in either direction
   (`tools/required_checks_gate.py:1-24`). `python tools/required_checks_gate.py --help` lists `--intent`,
   `--protection-file`, `--repo`, `--branch` and `--json`; `--protection-file` diffs against a captured payload
   without calling GitHub.

The `verify-receipt` check is not in that set. The receipt gate is in a measurement period and its hosted check
is not required (`docs/archive/RECEIPT-GATE.md:3-4`), so do not expect a missing receipt to stop a merge.

## 5. The audit log

Each blocked push appends one JSON line to `state/SECURITY-AUDIT.log` under your repository root, which is
git-ignored (`hooks/CLAUDE.md:46`). Every line stores the SHA-256 of the line before it, and the first line stores `GENESIS`
(`hooks/pre-push-policy.sh:440,1887-1902`). Check the chain with:

```bash
bash hooks/pre-push-policy.sh --verify-audit-log
```

Output on an intact one-entry log, from a real run:

```
Audit log verification OK (1 entries)
```

Pass a path as the second argument to check a log somewhere else (`hooks/pre-push-policy.sh:3836-3841`). A path that
does not exist prints `Error: Audit log not found at <path>` and exits 1. The chain shows edits and truncation to
anyone who does not also rewrite the sidecar `state/.audit-tail-hash`. It does not stop an attacker who controls
the machine, so ship the log somewhere you trust if you need that (`docs/archive/HOOK-INSTALL.md:138`).

## Troubleshooting

- **Hook never runs.** Check that `.git/hooks/pre-push` exists and is executable. In a linked worktree the hooks
  directory is the shared one under the main repository's git directory, not a folder in the worktree
  (`bin/cli.js:449-466`).
- **Every push blocks on a missing file.** You are on a fresh repository without the five generated files in
  section 1. Regenerate and commit them.
- **A gate reports its script missing.** `tools/` exists but that gate's script is gone, which blocks by design
  (`hooks/CLAUDE.md:62-65`). Restore the script. If the repository has no `tools/` at all, those gates are skipped
  and logged as `*_skipped_no_aesop_tools`.
- **Push blocked with `aesop_root_pin_mismatch`.** `AESOP_ROOT` is set to a different directory than the repository
  being pushed. Unset it or point it at this repository (`hooks/pre-push-policy.sh:3856-3859`).
- **A gate edit behaves as if it did nothing.** Gate scripts run from the trunk's copy on `origin`, not from your
  branch, so a gate change takes effect for pushes after it merges (`hooks/pre-push-policy.sh:260-265`).
