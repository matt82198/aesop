<p align="center">
  <img src="https://cdn.jsdelivr.net/npm/@matt82198/aesop@0.11.0/assets/logo.png" alt="Aesop" width="420">
</p>

<p align="center">
  <em>A kernel for running fleets of coding agents: an iteration counts only when it leaves a receipt that a gate can check.</em>
</p>

<p align="center">
  <a href="https://cdn.jsdelivr.net/npm/@matt82198/aesop@0.11.0/assets/clips/lens-fanout-haiku-pin.mp4">Watch the wave opening and audit fleet fanout (MP4, 0.5 MB)</a>
</p>

<p align="center">
  <a href="https://github.com/matt82198/aesop/actions/workflows/ci.yml"><img src="https://github.com/matt82198/aesop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/v/@matt82198/aesop" alt="npm version"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/dm/@matt82198/aesop" alt="npm downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>

> **Status.** Version 0.11.0 (`package.json:3`). The receipt gate is in a measurement period: the hosted `verify-receipt` check is not required (`docs/archive/RECEIPT-GATE.md:3-4`). The hooks that dispatch lanes and bind claims to evidence live in private repositories and are not shipped here (`docs/PROVENANCE.md:20-26`, [what is public and what is not](./docs/provenance/public-and-private.md)).

## Blueprint

[docs/README.md](./docs/README.md) is the blueprint index: one page per topic, each claim cited to the code or the record behind it.

- [Concepts](./docs/concepts/receipts-loop.md): why a lane counts only when it leaves a receipt a gate can check.
- [Guides](./docs/guides/verify-receipt.md): do something, such as verify a receipt offline.
- [Reference](./docs/reference/receipt-format.md): look something up, such as the receipt fields and the gates.
- [Provenance](./docs/provenance/README.md): what you can check for yourself, and how.
- [CONTRIBUTING.md](./CONTRIBUTING.md): adopt it, contribute to it, and what a pull request must pass.
- [SECURITY.md](./SECURITY.md): how to report a vulnerability.

## What it is

Aesop is a kernel. It is not a finished autonomous developer, and it does not know your repositories, rules, or accounts. It ships the parts that run the loop: dispatch, gates, receipts, merge, watchdog, durable state, and a dashboard. You fill in the rest.

The one rule it is built around: **a lane that reports success without a receipt a gate can verify has not passed.** The loop looks like this.

```text
lane in a worktree -> pre-push gates -> run the checks in a throwaway worktree
  -> sign the result (ed25519 or hmac-sha256) -> post it on the head sha
  -> verifier recomputes the tree hash from its own checkout -> exit 0 / 1 / 2
```

- The pre-push policy runs the gates and runs `check_emit_receipt` last, after every other gate has passed (`hooks/pre-push-policy.sh:4055`, defined at `hooks/pre-push-policy.sh:2363`, called at `hooks/pre-push-policy.sh:4058`).
- The emitter signs with Ed25519 or HMAC-SHA256 and posts the receipt on the head sha (`tools/emit_receipt.py:3-8`).
- The verifier recomputes the tree hash from its own checkout and never trusts the number in the receipt. It exits 0 for valid, 1 for invalid and 2 when it cannot evaluate (`tools/verify_receipt.py:3`).
- A passing receipt proves that the key holder ran the named parts on that tree and got those exit codes. It does not prove the parts were run honestly (`tools/verify_receipt.py:5-9`).

The pre-push policy holds 23 checks <!-- count: grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh -> 23 -->, one of them the secret scan that blocks a push (`hooks/pre-push-policy.sh:796`).

### System diagram

Blue boxes ship in the npm package. Yellow boxes are yours to provide. Grey dashed boxes are examples that show one way to structure the system.

![Kernel boundary](https://cdn.jsdelivr.net/npm/@matt82198/aesop@0.11.0/docs/img/kernel-boundary.svg)

Mermaid source: [docs/img/kernel-boundary.mmd](./docs/img/kernel-boundary.mmd)

## Try it in two minutes, no API keys

You need Node.js 18 or newer (`package.json:54-55`), Python 3.11 or newer (`pyproject.toml:19`) and git.

```bash
npx @matt82198/aesop dash --demo
```

That starts the dashboard on a seeded, self-identifying demo snapshot (`bin/cli.js:584`, `ui/serve.py:32-37`). The [quickstart](./docs/quickstart.md) shows the real output, then signs a receipt on your own checkout and verifies it offline.

## What ships, what you define, what is an example

Every path below exists on the main branch.

| Class | Piece | Where (path) | What it does for you |
|---|---|---|---|
| Ships | Dispatch loop | `driver/wave_loop.py` | Runs one wave through the AgentDriver backends (`driver/wave_loop.py:1-8`). |
| Ships | Worker driver | `driver/claude_code_driver.py` | Runs a coding agent in a worktree. Other backends sit beside it in `driver/`. |
| Ships | Gates and pre-push policy | `hooks/pre-push-policy.sh` | Runs the push-time checks. The CLI copies it into the hooks of the repo it scaffolds (`bin/cli.js:1492`). |
| Ships | Secret scan | `tools/secret_scan.py` | Blocks a push when staged files contain secrets. |
| Ships | Receipts | `tools/emit_receipt.py`, `tools/verify_receipt.py` | Sign a record of which local checks ran, and verify it offline. |
| Ships | Merge | `tools/auto_merge.py` | Batch merge of green PRs, with conflict fixing (`tools/auto_merge.py:2`). Uses `gh`. |
| Ships | Watchdog and self-heal | `daemons/run-watchdog.sh`, `daemons/selfheal.sh` | Backs up and scans fleet work on a timer and honors a halt switch (`daemons/run-watchdog.sh:2-12`). |
| Ships | Durable state | `state_store/store.py` | Append-only SQLite store in WAL mode (`state_store/store.py:1-6`). |
| Ships | Event log | `tools/event_store/store.py` | Hash-chained JSON Lines log (`tools/event_store/store.py:1`, `tools/event_store/store.py:3-4`, `tools/event_store/store.py:16`). |
| Ships | Dashboard | `ui/serve.py`, `ui/web/dist/index.html` | Live fleet view, with a zero-key demo mode (`ui/serve.py:32-37`). |
| Ships | Monitor and MCP server | `monitor/collect-signals.mjs`, `mcp/server.mjs` | Collects health signals. The MCP server is read-only (`mcp/CLAUDE.md:26`). |
| Ships | Skills | `skills/power/SKILL.md`, `skills/buildsystem/SKILL.md` | The CLI installs them into `~/.claude/skills/` unless you pass `--no-skills` (`bin/cli.js:570`). |
| Ships | Brain templates | `CLAUDE-TEMPLATE.md`, `docs/MEMORY-TEMPLATE.md` | Starting shape for your own CLAUDE.md and memory index. |
| Ships | CI templates | `templates/ci/ci-hosted.yml` | Starting workflows. Pick a mode with `npx @matt82198/aesop init --ci-mode` (`bin/cli.js:522`). |
| Ships | Claude Code hooks | `hooks/claude/force-model-policy.mjs` (plus two more) | Ship as files. Wiring them into your Claude Code settings is yours. |
| Ships | Config example | `aesop.config.example.json` | Example `aesop.config.json` to copy and edit. |
| You define | Repositories | `aesop.config.example.json` (`repos`) | The repos the fleet works on. The example uses placeholders. |
| You define | Brain | `~/.claude` (`brain_root`, see [docs/CONFIGURE.md](./docs/CONFIGURE.md)) | Your rules, memory, and brain-side hooks. Not in the package. |
| You define | Machinery repo (optional) | `daemons/conductor-root.sh` (`CONDUCTOR_ROOT`) | A directory of fleet state that you own. Set it explicitly. |
| You define | Keys and accounts | `aesop.config.example.json` (`seats`), `driver/anthropic_transport.py` | Claude Code login, `gh` auth, and API keys only if you use an API backend. |
| You define | Domain map | `CLAUDE-TEMPLATE.md` | Your project map and per-domain rules. |
| You define | Brain-side hooks | `~/.claude/hooks/` (not in this repo) | Hooks for your own environment. The kernel does not ship them. |
| Example | Aesop's own root CLAUDE.md | `CLAUDE.md` | One layout for a domain map. Not in the package. |
| Example | Machinery map | `docs/MACHINERY-MAP.md` | Maps this repo's gates and daemons. It does not describe your setup. |
| Example | First-wave replay | `examples/first-wave-baseline/` | A recorded wave to study. Not in the package. |
| Example | Scanner template | `scan/fleet-scan.example.mjs` | An IOC scanner to copy and configure. |
| Example | Lane contract | `LANE-CONTRACT.md` | One project's dispatch contract. Not in the package. |
| Example | This repo's metrics and tests | `stats.json`, `tests/` | Counts and suites for Aesop itself. Not in the package. |

## Fill in the gaps

1. **Install the kernel.** `npx @matt82198/aesop my-fleet --name "api" --repos "/path/to/repo"`, then `npx @matt82198/aesop doctor` to see what is still missing (`bin/cli.js:514`, `bin/cli.js:516`).
2. **Point it at your repos.** Copy `aesop.config.example.json` to `aesop.config.json` and set `repos`. Field reference: [docs/CONFIGURE.md](./docs/CONFIGURE.md).
3. **Write your brain.** Copy `CLAUDE-TEMPLATE.md` to `~/.claude/CLAUDE.md` and edit it. Add a `MEMORY.md` built from `docs/MEMORY-TEMPLATE.md`. `/power` reads both.
4. **Name a machinery repo (optional).** Set `CONDUCTOR_ROOT` to a directory you own. The watchdog derives a sibling fleet root only for the canonical `aesop` checkout and otherwise refuses to start (`daemons/run-watchdog.sh:517-519`).
5. **Provide keys and accounts.** Run `gh auth login` for merges and sign in to Claude Code for workers. Add API keys only if you use an API backend.
6. **Run one wave.** `npx @matt82198/aesop watch`, then `/power`, then `/buildsystem`. [docs/FIRST-WAVE.md](./docs/FIRST-WAVE.md) walks through it.

## Evidence and limits

- **Incidents.** The generated incident ledger counts 41 incidents across eight classes <!-- count: sum of the class counts in the Summary list of docs/INCIDENTS.md -> 41 -->, 3 of them gate-activation and 1 fake-green (`docs/INCIDENTS.md:9-16`). [How to read it](./docs/provenance/incidents.md).
- **Trap tests.** [tests/test_traps.py](./tests/test_traps.py) reproduces patterns of agent deception, such as a test that skips real validation.
- **Event log.** Tamper-evident under `verify`, not tamper-proof (`docs/concepts/event-store.md:6`, `docs/concepts/event-store.md:139`).
- **Per-project stores.** The tooling ships. This repo does not claim the live cutover (`docs/concepts/per-project-stores.md:3-6`).
- **Dependencies.** The root `package.json` has no `dependencies` key. The receipt tools are standard library, with `cryptography` optional for Ed25519 (`tools/emit_receipt.py:3`).
- **Private parts.** The lane dispatch hooks and the claim-binding hook are brain-side and not shipped (`docs/PROVENANCE.md:20-26`).
- **Cost figures.** Cost comparisons in the older docs rest on a private archive (`docs/ab-cost-dataset.md:6`). Treat them as author-reported until the data is published.

## Learn more

- **[Documentation home](./docs/README.md)** - the map of every page.
- **[Quickstart](./docs/quickstart.md)** - the zero-key demo, then sign and verify a receipt.
- **Concepts** - [receipts loop](./docs/concepts/receipts-loop.md), [signed receipts](./docs/concepts/signed-receipts.md), [event store](./docs/concepts/event-store.md), [per-project stores](./docs/concepts/per-project-stores.md), [stage lanes](./docs/concepts/stage-lanes.md), [crash-only operation](./docs/concepts/crash-only.md).
- **Guides** - [verify a receipt](./docs/guides/verify-receipt.md), [verify an event store](./docs/guides/verify-event-store.md), [install](./docs/INSTALL.md), [first wave](./docs/FIRST-WAVE.md).
- **Reference** - [receipt format](./docs/reference/receipt-format.md), [stage matrix](./docs/reference/stage-matrix.md), [machinery map](./docs/MACHINERY-MAP.md).
- **Trust** - [provenance and evidence](./docs/provenance/README.md), [releasing](./docs/RELEASING.md), [SECURITY.md](./SECURITY.md).
- **History** - [CHANGELOG.md](./CHANGELOG.md).

## Contributing

Bug reports, questions, documentation fixes and pull requests are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md). To report a vulnerability, read [SECURITY.md](./SECURITY.md) first.

## License

Aesop Kernel is open source, licensed under the **[MIT License](./LICENSE)**.

Versions 0.8.0 through 0.11.0 were released under the PolyForm Noncommercial License 1.0.0. Versions up to 0.7.0 were released under the MIT License. Releases already published keep the terms they were published under.

Contributions are accepted under the MIT License, with no CLA. See [CONTRIBUTING.md](./CONTRIBUTING.md).

Copyright 2026 Matt Culliton.

## Citing

If you use Aesop Kernel, please cite it using the metadata in [CITATION.cff](./CITATION.cff) (GitHub
renders a "Cite this repository" button from this file). It also lists the preferred research
citation, *The Receipts Loop* (manuscript draft, arXiv submission pending).

## Contact

> **Contact policy.** I am not accepting acquisition offers, recruiter inquiries, or cold calls. Inquiries regarding licensing or commercial usage must go to matt82198@gmail.com. All other outreach will be ignored.
