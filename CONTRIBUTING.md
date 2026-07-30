# Contributing to Aesop

Thank you for your interest in Aesop! Your feedback, questions, and bug reports genuinely help make the orchestration harness more reliable and observable.

## A note on the license

Aesop is **MIT-licensed** under the [MIT License](./LICENSE). This means contributions via pull requests are warmly welcome.

What that means in practice:

- **Issues, bug reports, and discussion are warmly welcome** — they're the best way to contribute and shape the project.
- **Code patches via pull request are welcome** — open a PR with your changes, and the maintainer will review it.

The rest of this guide documents how to run and test Aesop locally (useful for anyone using it) and how the maintainer's own development loop works.

## Getting Started

### Prerequisites

- **Git** v2.40 or later
- **Bash** v4.0 or later
- **Python** 3.10+ (for dashboard and tools)
- **Node.js** v18+ (optional; required for monitor and dashboard extras)
- **Claude Code CLI** (to test orchestration workflows)

### Setup

1. **Clone** the repository (for local use and testing):
   ```bash
   git clone https://github.com/matt82198/aesop.git
   cd aesop
   ```

2. **Create a configuration file**:
   ```bash
   cp aesop.config.example.json aesop.config.json
   # Edit aesop.config.json with your local paths
   ```

3. **Test the watchdog locally** (one-shot mode):
   ```bash
   bash daemons/run-watchdog.sh --once
   ```

4. **Launch the web dashboard**:
   ```bash
   python ui/serve.py
   ```
   Visit `http://localhost:8770` to verify the interface.

## Development Workflow (maintainer reference)

The conventions below describe how the maintainer's own `/buildsystem` loop develops Aesop. They're documented here for transparency and for anyone the maintainer arranges a code collaboration with.

### Branch Discipline

- Create feature branches from `main`: `git checkout -b feature/your-feature`
- Use `docs/` prefix for documentation: `git checkout -b docs/update-guides`
- Use `fix/` prefix for bug fixes: `git checkout -b fix/watchdog-stall`
- Never push directly to `main`; use pull requests only.

### Commit Messages

Follow conventional commit format:

```
type(scope): short summary

Optional longer explanation of the change. Describe the why, not just the what.

Co-Authored-By: Your Name <your-email@example.com>
```

**Type** should be one of:
- `feat`: New feature or capability
- `fix`: Bug fix
- `docs`: Documentation only
- `refactor`: Code refactoring (no behavioral change)
- `perf`: Performance improvement
- `test`: Test additions or fixes
- `chore`: Tooling, CI/CD, dependencies

**Scope** is optional but recommended (e.g., `watchdog`, `dashboard`, `monitor`, `docs`).

### Testing

Aesop is designed for integration testing rather than extensive unit tests. Before submitting:

1. **Test watchdog backup locally** (`--once` mode):
   ```bash
   bash daemons/run-watchdog.sh --once
   ```
   Verify `state/FLEET-BACKUP.log` for success.

2. **Test the dashboard**:
   ```bash
   python ui/serve.py
   # Visit http://localhost:8770
   # Verify all panels load and refresh every 3s
   ```

3. **Test monitor signal collection** (if changes affect monitor):
   ```bash
   node monitor/collect-signals.mjs
   ```

4. **Manual orchestration test** (if changes affect dispatch):
   - Use Claude Code with the modified Aesop configuration
   - Spawn a test Haiku subagent and verify heartbeat updates

## Proposing Behavioral Changes

When a change modifies **operational rules, agent configuration, monitoring behavior, or memory conventions** (anything that changes how the orchestration harness operates), it is a behavioral change and follows this process in the maintainer's development loop:

1. **Stage changes via PROPOSALS.md**: The monitor's signal collection (`monitor/collect-signals.mjs`) can emit structured proposals for rule or behavior changes. These proposals enter a human-review queue in `monitor/PROPOSALS.md`.

2. **Complete the PR template**: Use the **"Behavioral change?"** section in [`.github/pull_request_template.md`](.github/pull_request_template.md) to document:
   - Which rule or behavior changed
   - Impact radius (which agents/components are affected)
   - How the change was tested
   - Rollback plan

3. **Reference the review checklist**: Reviewers will use [`docs/BEHAVIORAL-PR-REVIEW.md`](docs/BEHAVIORAL-PR-REVIEW.md) to verify:
   - Rule alignment with cardinal rules
   - Blast radius assessment
   - Verification and testing steps
   - Rollback & recovery plan
   - Single-writer discipline (if PROPOSALS.md/ACTIONS.log touched)

4. **Merge only after review**: Behavioral changes go through the standard PR review but require explicit mention of rule changes and impact assessment before approval.

**See also:** `monitor/CHARTER.md` for how the monitor categorizes signal actions (AUTO vs PROPOSE tier).

## Security & Code Review

### Secret Scanning

This repository has **zero tolerance for credentials, API keys, or private paths**. Before committing any change:

1. **Review your changes** for any hardcoded secrets, local paths, or private identifiers.
2. **Run your own secret scanner** (e.g., `git secrets`, `truffleHog`):
   ```bash
   git diff HEAD~1 | my-secret-scanner
   ```
3. **Never commit** `aesop.config.json` or `.env` files (they are git-ignored for this reason).

### Code Style

- **Shell scripts** (bash): POSIX-compatible where possible; include comments for complex logic.
- **Python**: Follow PEP 8; prefer readability over cleverness.
- **JavaScript**: Use modern ES6+ features; Node.js 18+.
- **Markdown**: Hard-wrap at 100 characters; use clear headings and code blocks.

### Documentation

- Update README.md if you add new features or change usage patterns.
- Add entries to CHANGELOG.md (Unreleased section) for user-facing changes.
- Write inline comments for non-obvious logic.
- Link to related docs/guides when relevant.

## Proposing a Change

Because the license doesn't permit outside code patches to be merged, the path for proposing a change is discussion-first rather than a pull request:

1. **Open an issue** describing the change:
   - What's broken, missing, or could be better.
   - The motivation and, where useful, a sketch of the design.
   - Reference any related issues (e.g., "Relates to #123").

2. **Discuss it** with the maintainer. Many changes can be implemented directly by the maintainer once the need is clear.

3. **For substantial collaboration**, reach out to arrange contribution terms directly — code contributions can be accommodated by separate agreement outside the standard license.

The sections below list the kinds of improvements the project most values — all of them are great topics for an issue or discussion.

## Areas Where Feedback Helps Most

### High-Priority
- **Bug reports**: Any issue blocking watchdog, dashboard, or orchestration.
- **Documentation gaps**: Workflows, troubleshooting, or patterns that are unclear.
- **Dashboard feedback**: Missing panels, data-visualization ideas, UX friction.

### Medium-Priority
- **Monitor ideas**: Custom signal collectors, better drift detection.
- **Daemon behavior**: Backup-cycle timing, error-handling edge cases.
- **Test coverage gaps**: Workflows that aren't well exercised.

### Lower-Priority
- **Cosmetic notes**: UI refinements, theme customization.
- **Platform quirks**: Windows-specific paths, macOS-specific issues.

## Questions or Ideas?

- Open an **issue** to discuss a feature or report a bug.
- Post questions in **Discussions** (if enabled).
- Reference relevant documentation (CARDINAL-RULES.md, DISPATCH-MODEL.md, etc.).

## Code of Conduct

We are committed to providing a welcoming and inclusive environment. Please treat all contributors with respect, provide constructive feedback, and help foster a collaborative community.

---

**Thank you for contributing to Aesop!** Your improvements make the orchestration harness more reliable, observable, and cost-effective for everyone.
