# LANE-CONTRACT-MIN.md — arm B (walls only) of the lane-contract A/B
Lever in the brain; ledger state/lane-ab.jsonl. A fault (hook denial or failed receipt) relaunches this lane with the full LANE-CONTRACT.md.

## Walls (enforced by hook or gate; violating one is a fault)
- Work only in your assigned sibling worktree (`$AESOP_ROOT-wt-<name>`). Never touch the primary tree.
- Never `git stash`. Never `--no-verify` or `-n` on commit or push. Never disable git hooks.
- Never `--admin`, never `--auto`, never force-push.
- Never author files through a Bash heredoc. Never use `[[...]]` tokens.
- No credential hunting. No env stores. Never write `settings.local.json`.
- Never read session transcript `.jsonl` files.
- Never start background runs (`run_in_background`, nohup, detached processes).
- Never kill a process you did not start by PID from your own CommandLine.
- Secret scan before every push: `python tools/secret_scan.py --staged` must exit 0.
- Commit messages: write the message with the Write tool, then `git commit -F <file>`. End it with `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`. This applies to commits you write; merge commits GitHub creates through update-branch carry no trailer and are not a violation.

## Report
- Paste literal command output for every command you cite, including `rc=` lines. Do not describe what a command would do.
- Name the PR number and confirm it with `gh pr view <N> --json state,headRefOid`.
- A hook denial is a stop: report it verbatim and do not work around it.
