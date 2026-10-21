# Case Study: Portfolio Site Build with Aesop

## What Happened (Timeline)

| Time | Event | Duration |
|------|-------|----------|
| 17:09 | User prompt: "build me a portfolio site" | — |
| 17:09–17:20 | 6 discovery scouts (GitHub API, Medium, LinkedIn, Reddit ×2, Playwright fallback, repo deep-dive) run in parallel; 4 ranked decision points surfaced | 11 min |
| 17:20–17:24 | User answers 4 prioritization questions | 4 min |
| 17:24–17:36 | 10 Haiku agents (scaffold + 8 section builders on file-disjoint Astro components + integrator) execute | 12 min |
| 17:36–17:37 | 9th section added from late Reddit data; Astro build green in 914ms | 1 min |
| 17:37–17:40 | Playwright screenshot proof round 1: CSS 404 detected (styles linked as `/src/` instead of `/dist/`) | 3 min |
| 17:40–17:42 | Repair agent fixes CSS paths; proof round 2 validates fix | 2 min |
| 17:42–17:48 | GitHub agent: secret-scan (10,066 files clean), create public repo, merge PR #1 | 6 min |
| 18:04 | GitHub Pages deployed and live at https://matt82198.github.io/ (verified 200 status) | **55 min total** |

## The Architecture

**Parallel discovery with verified facts only**: Six independent scouts (HTTP fallback to Playwright for bot-blocked sites) collected portfolio inspiration, ranked by relevance; each returned structured data on a single contract. No hallucination—every section claim traces to a source.

**File-disjoint fan-out**: 8 section agents each owned one `.astro` component with zero file overlap; no merge conflicts, no serialization bottleneck. Integrator assembled in 12 minutes.

**Proof-driven QA**: Screenshot verification caught a real ship-blocker—styles in dist/ referenced `/src/` paths that vanished at build time. No visual inspection could have found this; Playwright proved it was broken, repair proved it was fixed.

**Gated ship**: Secret-scan gate (10,066 files) ran before public repo creation; zero credentials leaked. PR merged, Pages deploy triggered, liveness confirmed.

**Orchestrator final-catch**: Fable verified theme coherence across 9 sections after assembly; caught one color inconsistency and one missing accent variant before ship.

## What Failed (and Was Caught)

The site rendered unstyled in production. Astro build succeeded locally (914ms), but the distributed `.astro` component agents—writing styles inline—used `import` statements pointing to source paths. After minification and bundling into `/dist/`, those paths no longer existed. The site shipped with no CSS.

Playwright screenshot automation caught this in production-like conditions (rendering against the compiled bundle). A repair agent updated path resolution, regenerated the build, and Playwright proved the fix before merge. This is not a testing infrastructure story—this is proof automation as a first-class gate.

## Numbers

- **Wall-clock time**: 55 minutes (first prompt to live site)
- **Total agents used**: ~30 (6 scouts, 10 builders, 1 repair, 1 GitHub ops, 3 proof/integration, 9 audits)
- **Build fleet**: 10 Haiku agents (scaffold + 8 section builders + integrator)
- **Build time**: 12 minutes (including inter-agent coordination)
- **Sections delivered**: 9
- **Proof rounds**: 2 (CSS 404 caught in round 1; fix validated in round 2)
- **Ship-blockers caught**: 1 (CSS 404)
- **Files secret-scanned**: 10,066 (0 issues)
- **Subagent tokens**: ~328K
- **PR**: #1, merged
- **Deploy**: GitHub Pages, live 200 status

## How to Reproduce

1. Fork this repo or set up Aesop in your environment.
2. Run the [buildsystem skill](../skills/CLAUDE.md) for a portfolio project.
3. Provide 4–6 source URLs or inspiration topics; Aesop will dispatch 6 discovery scouts.
4. Approve the ranked backlog (user decision round).
5. Aesop dispatches the 10-agent build workflow in parallel on Astro or your framework.
6. Screenshot proof runs automatically; Aesop fixes any visual defects and re-proves.
7. Secret-scan gate runs; if clean, Aesop creates the public repo and merges the PR.
8. Pages (or your host) deploys automatically.

The 55-minute bound is achievable when discovery sources are web-accessible (not gated PDFs) and the component schema is well-defined (Astro's `.astro` files are ideal; monolithic templates require serialization).

