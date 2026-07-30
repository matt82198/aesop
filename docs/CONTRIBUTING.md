# Contributing to Aesop

Aesop is **MIT-licensed** under the [MIT License](../LICENSE). Contributions via pull request are warmly welcome.

## Contribution Policy

**Code patches are welcome and encouraged.** Open a pull request with your changes, and the maintainer will review it. Feedback and collaboration are also valued:

- **Issues and bug reports** — describe what's broken or confusing.
- **Discussion and ideas** — feature requests, design critiques, use-case questions.
- **Code snapshots for discussion** — propose changes in issues or discussions without expecting merge.

Code changes are made by the maintainer at their discretion. If you'd like to propose a change, open an issue first — describe the problem, your approach, and why it matters.

## Running the Test Suites

Aesop has three test suites:

```bash
# Node.js tests (orchestration skills, monitor, UI API)
npm run test:node

# Python tests (state_store, log rotation, secret-scan)
npm run test:py

# Bash tests (daemons, watchdog, hook policy)
npm run test:sh

# All suites
npm run test:all
```

Each suite is self-contained and idempotent. Run them before proposing any changes.

## Workflow (for Maintainers)

Aesop develops itself via its own `/buildsystem` wave cycle. The process:

1. **One issue per branch** — `feature/issue-name` branches only (never main).
2. **Commit and push** — each commit triggers the pre-push hook.
3. **Secret-scan gate** — `hooks/pre-push-policy.sh` blocks any credentials before they leave your machine.
4. **Pull request** — PR runs CI and waits for merge-train coordination.
5. **Checkpoint** — STATE.md and BUILDLOG.md capture wave state; both are git-committed and survive wipes.

See [docs/ARCHITECTURE.md](./ARCHITECTURE.md) for the wave cycle diagram and [docs/HOW-THE-LOOP-WORKS.md](./HOW-THE-LOOP-WORKS.md) for a concrete walkthrough.

## Code Style

**Python**: Stdlib-first (no external deps except asyncio). Docstrings required; snake_case for functions and variables. See `tools/secret_scan.py` and `state_store/` for examples.

**Node.js/JavaScript**: Node stdlib for tooling only (no external packages). Use async/await. See `daemons/`, `monitor/`, and `bin/` for patterns.

**Bash**: POSIX-safe (no Bashisms; works in Git Bash on Windows). Sourceable scripts must guard with `[[ "${BASH_SOURCE[0]}" == "${0}" ]]`. See `hooks/pre-push-policy.sh` for structure.

## Attribution

Commits are authored with your git user.name. The repo signs all commits and uses co-author trailers for multi-person changes:

```
git commit -m "message

Co-Authored-By: Alice <alice@example.com>
Co-Authored-By: Bob <bob@example.com>"
```

## License & DCO

By proposing changes, you affirm that you own or have the right to contribute your work and agree to license it under the MIT License. See [LICENSE](../LICENSE) for the full terms.

## Questions?

Open an issue or discussion on GitHub. Read [docs/GOVERNANCE.md](./GOVERNANCE.md) for details on the decision-making process and [docs/RELIABILITY.md](./RELIABILITY.md) for the engineering principles behind Aesop.
