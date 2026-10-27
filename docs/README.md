# Documentation home

This is the map of the Aesop docs. Aesop is a kernel for running fleets of coding agents. Its one rule is
that an iteration counts only when it leaves a receipt that a gate can check. The [project README](../README.md)
says what ships and what you fill in.

The pages are grouped by what you want to do: try it, understand it, do something, look something up, or
decide whether to trust it. Every page here exists on the main branch.

A note on age. The folders `concepts/`, `guides/`, `reference/` and `provenance/` hold pages written against
the current tree. The loose `docs/*.md` pages listed below are older. They stay linked until a folder page
replaces them, so check their claims against the code before you rely on them. The rewrite plan is [plans/OSS-BLUEPRINT-DOCS.md](./plans/OSS-BLUEPRINT-DOCS.md).

## Try it

| Page | What it gives you |
|---|---|
| [Quickstart](./quickstart.md) | The zero-key dashboard demo, then sign a receipt on your checkout and verify it offline. |
| [Demo flow](./DEMO.md) | An init, prime and demo walkthrough of a first complete flow. |
| [Reproducing Aesop](./reproduce.md) | How to reproduce the project's claims from a clean checkout or an installed package. |

## Understand it

| Page | What it explains |
|---|---|
| [The receipts loop](./concepts/receipts-loop.md) | Why a lane counts only when it leaves a receipt a gate can check. |
| [Signed receipts](./concepts/signed-receipts.md) | The receipt envelope, the signature schemes and the tree binding. |
| [Event store](./concepts/event-store.md) | The append-only log where each line records the hash of the previous line. |
| [Per-project stores](./concepts/per-project-stores.md) | One chain per project, the registry, and what is and is not built. |
| [Stage lanes](./concepts/stage-lanes.md) | The pipeline of short per-stage contracts a change moves through. |
| [Crash-only operation](./concepts/crash-only.md) | Why restart from disk is the recovery path, and where the model stops. |
| [Receipt gate](./RECEIPT-GATE.md) | The signed-receipt design, its increments and its current status. |
| [Architecture](./ARCHITECTURE.md) | The wave cycle and the components around it. |
| [Two swappable seats](./MICROKERNEL.md) | The worker seat and the orchestrator seat, and what a model swap changes. |
| [Durable checkpointing](./CHECKPOINTING.md) | STATE.md and BUILDLOG.md, recovery on resume, log rotation. |
| [How the wave loop works](./HOW-THE-LOOP-WORKS.md) | One wave from ranking to close. |
| [Cardinal rules](./CARDINAL-RULES.md) | The foundational operating principles for orchestration. |
| [Governance](./GOVERNANCE.md) | Single-writer files, heartbeats and the inbox pattern. |

## Do something

| Page | What it walks you through |
|---|---|
| [Install](./INSTALL.md) | Prerequisites, the `npx` scaffold, and what it creates. |
| [Configure](./CONFIGURE.md) | `aesop.config.json`, repos, ports and the brain root. |
| [First wave](./FIRST-WAVE.md) | Running `/power` then `/buildsystem` on the replay kit in [examples/first-wave-baseline](../examples/first-wave-baseline/README.md). |
| [Install the pre-push hook](./HOOK-INSTALL.md) | Setting up `hooks/pre-push-policy.sh`. |
| [Verify a receipt](./guides/verify-receipt.md) | Check a signed receipt offline. |
| [Verify an event store](./guides/verify-event-store.md) | Check a hash chain and see what a broken one looks like. |
| [Adoption checklist](./ADOPTION-CHECKLIST.md) | Step-by-step acceptance criteria for adopting Aesop in a repository. |
| [Aesop in any repository](./ANY-REPO.md) | A portability walkthrough. See also [porting](./PORTING.md). |
| [Pick a CI mode](./CI-MODES.md) | The hosted, self-hosted and local-receipt-gate modes. |
| [Review a behavioral PR](./BEHAVIORAL-PR-REVIEW.md) | A checklist for pull requests that change rules or orchestration behavior. |
| [Debug an agent failure](./FORENSICS.md) | Making agent failures git-bisectable. |
| [Restore after a wipe](./RESTORE.md) | Reconstituting Aesop and a fleet on a new machine. |

## Look something up

| Page | What it lists |
|---|---|
| [Receipt format](./reference/receipt-format.md) | The receipt fields and the verifier exit codes. |
| [Stage matrix](./reference/stage-matrix.md) | What each stage lane may take in, produce, run and hand back, by change category. |
| [Stage contracts](./STAGE-CONTRACTS.md) | The older stage-by-category templates for dispatch. |
| [CI failure catalog](./CI-CATALOG.md) | Recurring CI failures, one recipe per category. |
| [Machinery map](./MACHINERY-MAP.md) | An evidence-bound map of gates, daemons, stores and lane contracts, with known gaps. |
| [Configuration reference](./CONFIGURE.md) | Config fields and environment variables. |
| [Test strategy](./TESTING.md) | How the test suites are organized. |

## Decide whether to trust it

| Page | What it answers |
|---|---|
| [Provenance and evidence](./provenance/README.md) | What here you can check for yourself, and how. |
| [Public and private](./provenance/public-and-private.md) | Which parts ship in this repository and which live in repositories you cannot read. |
| [Design decisions](./provenance/design-decisions.md) | The recorded decisions and the cost model, and which numbers you can reproduce. |
| [Incidents](./provenance/incidents.md) | How to read the incident ledger and what its counts do not prove. |
| [Gates fired](./provenance/gates-fired.md) | Which gates have a recorded blocking event. |
| [Refuted ledger](./provenance/refuted-ledger.md) | Claims that were measured and killed. |
| [Provenance record](./PROVENANCE.md) | The dated record of the commits behind the release. |

## The project

| Page | What it covers |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to set up, what a pull request must pass, and what the maintainer looks at. |
| [SECURITY.md](../SECURITY.md) | How to report a vulnerability. |
| [Releasing](./RELEASING.md) | How a release is cut and published. |
| [CHANGELOG.md](../CHANGELOG.md) | The per-release change list. |
| [Archive](./archive/README.md) | Retired pages, kept for history. |
