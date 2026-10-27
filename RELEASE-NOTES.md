# Release notes

These notes summarize each release in a few lines. [CHANGELOG.md](./CHANGELOG.md) is the full, per-PR record
and is the source for everything below. When the two disagree, the changelog is right.

Latest release: **0.11.0**, released 2026-10-22.

<!-- CMD: node -e "console.log(require('./package.json').version)"
OUTPUT: 0.11.0 -->
<!-- CMD: grep -n '^## \[' CHANGELOG.md | head -3
OUTPUT:
10:## [Unreleased]
14:## [0.11.0] - 2026-10-22
53:## [0.10.4] - 2026-10-22
-->

Unreleased changes: none recorded (`CHANGELOG.md:10-12`).

---

# aesop 0.11.0 - Release 2026-10-22

**Headline**: The hash-chained event store, a per-project event store plan, and a dated provenance record
(`CHANGELOG.md:14-16`).

## What changed

- **Event store v2.** Each event carries a sha256 `prev` field over the previous event, so edits to the log are
  detectable. This is tamper-evident, not tamper-proof (`CHANGELOG.md:20`).
  Related entries: per-project split of the frozen v1 log (#1281), init and append-only guard (#1282), read-only
  open of a v1 log (#1283), projections (#1284) (`CHANGELOG.md:21-27`).
- **Secret scan.** A range scan now covers every commit, and zero-file and empty-index cases are reported as
  failures (#1269) (`CHANGELOG.md:29`).
- **Receipts and gates.** #1264 changes the receipt phase, audit lock spill, exit-2 blocks and
  Linux-shape covered paths (`CHANGELOG.md:28`).
- **Packaging.** The npm tarball now ships `tools/receipt_pubkeys` (#1286) (`CHANGELOG.md:32`).
- **Provenance record.** `docs/PROVENANCE.md` and a signed receipt over it were added (`CHANGELOG.md:49-51`).
  Verify it with the command in [docs/provenance/public-and-private.md](./docs/provenance/public-and-private.md).

## Known limits in 0.11.0

- The event store ships with its tooling. These notes do not say the author's own logs have been moved to it.
- The license is PolyForm Noncommercial 1.0.0, which is source-available and not an OSI open-source license
  (`LICENSE:1`).

## Earlier releases


Every release before 0.11.0 is recorded in [CHANGELOG.md](./CHANGELOG.md), which lists the pull requests in each.
The per-release prose notes that used to follow here for 0.9.2 and earlier are in this file's git history.

