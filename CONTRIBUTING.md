# Contributing to Aesop

Aesop is open source under the [MIT License](./LICENSE) (`LICENSE:1`). Bug
reports, questions, documentation fixes and pull requests are all welcome. This page says how to set up,
what a pull request has to pass, and what the maintainer will look at.

## Before you start

- Open an issue first for anything larger than a typo or a one-file fix. Say what is broken or missing and
  what you would change. It saves both sides a rejected pull request.
- Report security problems privately, not as issues. See [SECURITY.md](./SECURITY.md).
- By opening a pull request you state that you wrote the change, or have the right to submit it, and that it
  may be distributed under the MIT License, the same license as the project. There is no separate contributor agreement
  in this repository.

## Set up

Requirements, with where each comes from:

| Requirement | Version | Source |
|---|---|---|
| Python | 3.11 or later | `pyproject.toml:19` |
| Node.js | 18 or later (22 is what `.nvmrc` pins for publishing) | `package.json:55`, `.nvmrc` |
| Git and a POSIX shell (Git Bash on Windows) | recent | the shell tests and `hooks/pre-push-policy.sh` need them |

<!-- CMD: grep -n 'requires-python' pyproject.toml
OUTPUT: 19:requires-python = ">=3.11" -->
<!-- CMD: cat .nvmrc
OUTPUT: 22 -->

```
git clone https://github.com/matt82198/aesop.git
cd aesop
pip install -r requirements-dev.txt
cp aesop.config.example.json aesop.config.json
```

`aesop.config.json` is local to you and must never be committed. The key meanings are in
[docs/CONFIGURE.md](./docs/CONFIGURE.md).

## Run the tests

There are three test harnesses (`package.json:80-86`), described in [docs/TESTING.md](./docs/TESTING.md):

```
npm run test:node
npm run test:sh
npm run test:py
npm run test:all
```

Run the one that covers what you changed, and `npm run test:all` before you ask for review. The Node and Python
runs go through isolation wrappers so a test cannot write to your real home directory or push to a real remote.
Do not run test files by hand without them.

Suite counts are not written in prose anywhere on purpose. To see the live inventory:

```
python tools/list_test_suites.py
```

## Install the pre-push hook

The pre-push policy runs the repository's checks before a push leaves your machine. Install it as described
in [docs/HOOK-INSTALL.md](./docs/HOOK-INSTALL.md). A local hook can be bypassed (`git push --no-verify`), so it is
a convenience and not a guarantee. The checks that count run in CI.

<!-- CMD: grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh
OUTPUT: 23 -->

The policy defines 23 `check_*` functions. Please do not bypass it. If a check blocks a change you believe is
correct, say so in the pull request instead.

### Forks do not inherit branch protection

The required CI checks are declared in `tools/required-checks.json` (`required_contexts`), but branch
protection is a setting on the GitHub repository, not a file. Your fork will not enforce them unless you set
them up. Run the tests yourself.

## Branches and commits

- Work on a branch, never on `main`. Use a prefix that says what it is: `feature/`, `fix/` or `docs/`.
- Commit messages follow conventional commits: `type(scope): summary`, with a subject of at most 72
  characters and no trailing period. Allowed types: feat, fix, refactor, test, docs, chore, ci, perf, style,
  build (`tools/commit_lint.py:8`).
- Check a message before you push:

```
python tools/commit_lint.py --message "docs(contributing): fix a link"
```

## What a pull request must include

The pull request template ([.github/pull_request_template.md](./.github/pull_request_template.md)) asks for a
description, the type of change and how you tested it. In addition:

- **Tests for behavior changes.** A test must run the code and check what it does. A test that only searches
  source text for a string does not count.
- **No secrets or personal paths.** Run `python tools/secret_scan.py --staged` before you commit. The pre-push
  policy runs it again.
- **A CHANGELOG entry** for anything a user would notice, under the Unreleased heading of
  [CHANGELOG.md](./CHANGELOG.md).
- **Behavioral changes are flagged.** A change to how the orchestration rules, hooks, monitor or dispatch
  behave is a behavioral change. Fill in the "Behavioral Change?" section of the template. Reviewers use
  [docs/BEHAVIORAL-PR-REVIEW.md](./docs/BEHAVIORAL-PR-REVIEW.md).
- **Generated files are regenerated, not hand-edited.** If you add a tool, run
  `python tools/gen_tool_index.py --regenerate`.

## Review

Changes to gate code, workflows, hooks and the lane contract are routed to the maintainer for review
(`.github/CODEOWNERS:9-19,41`). Required code-owner approval is not enabled yet (`.github/CODEOWNERS:3-5`), so a
merge does not wait on a second reviewer. Expect the maintainer to run the full test set and read the diff
against the pull request description.

## Code style

- **Python:** standard library first. A new third-party import needs a stated reason and an entry in
  `pyproject.toml`. Subprocess calls that read output must set `encoding='utf-8', errors='replace'`. Write
  docstrings.
- **JavaScript:** Node built-ins only at runtime. The root `package.json` has no `dependencies` key; its one
  entry is a dev dependency.
- **Shell:** POSIX-safe where possible, and it has to run in Git Bash on Windows.
- **Markdown:** ASCII only, short paragraphs, and every command shown has to run as written.

<!-- CMD: node -e "console.log(Object.keys(require('./package.json')).includes('dependencies'))"
OUTPUT: false -->

## About CLAUDE.md and LANE-CONTRACT.md

[CLAUDE.md](./CLAUDE.md) and [LANE-CONTRACT.md](./LANE-CONTRACT.md) are written for the maintainer's own
automated agents. They are public and you can read them, but you are not bound by them. Where they say a lane
"must" do something, that is an instruction to an agent, not a requirement on you. The rules that bind a pull
request are the ones on this page and the checks CI runs.

## Questions

Open an issue. For how decisions get made, read [docs/GOVERNANCE.md](./docs/GOVERNANCE.md).
