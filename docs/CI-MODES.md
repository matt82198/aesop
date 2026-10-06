# CI modes

aesop's merge pipeline is only as fast as the CI behind it. Hosted GitHub runners queue; a
self-hosted runner on an aesop box adds capacity; a locally produced, cryptographically
verified test receipt removes the dependence on remote CI capacity altogether. This page is
the adopter-facing surface for choosing between them: the config block, the scaffold flag,
the doctor table that says what *this* machine can run, and the runner installer.

**Stopgap note.** A self-hosted runner is capacity relief, not the design. It still runs
the whole suite on a remote scheduler's terms (queueing, cancellation, runner starvation).
The receipt gate -- run the shards locally, emit a signed receipt, let CI only *verify* the
receipt -- is the long-term shape. Until the receipt lane lands (branch
`feat/receipt-gate-increment-1`: `tools/emit_receipt.py`, `tools/verify_receipt.py`,
`.github/workflows/verify-receipt.yml`), `local-receipt-gate` is scaffold-refused and the
doctor reports it as pending. Everything in this document feature-detects those files; nothing
assumes they exist.

## The three modes

| mode | what runs where | trade-off |
|------|-----------------|-----------|
| `hosted` | every job on GitHub-hosted runners (`ubuntu-latest`, `windows-latest`) | zero setup, nothing required on your machine; capacity is GitHub's queue, Windows minutes are the bottleneck |
| `self-hosted-runner` | Linux shards hosted; the Windows job on your runner labels for same-repo PRs, on `windows-latest` for fork PRs | relieves the Windows bottleneck; your hardware runs PR code, so fork PRs are routed away and public repos must require approval for all external contributors |
| `local-receipt-gate` | hosted workflow plus `verify-receipt.yml`; shards run locally and emit a receipt CI verifies | no remote capacity on the critical path; needs the receipt tools (pending) and, on Windows, WSL or Docker for the Linux shards |

Modes compose: `local-receipt-gate` scaffolds as `["hosted", "local-receipt-gate"]` with the
receipt gate running alongside hosted CI until you flip it to `required`.

## Config: the `ci` block of `aesop.config.json`

```json
"ci": {
  "mode": ["hosted"],
  "windowsMatrix": "hosted",
  "receiptGate": "off",
  "selfHostedLabels": ["self-hosted", "windows", "aesop-box"]
}
```

| key | values | meaning |
|-----|--------|---------|
| `mode` | list over `hosted`, `self-hosted-runner`, `local-receipt-gate` | which CI shapes this repo opts into (non-empty, no duplicates) |
| `windowsMatrix` | `hosted`, `self-hosted`, `skip-on-pr` | where the Windows job runs; `self-hosted` requires `self-hosted-runner` in `mode` |
| `receiptGate` | `off`, `alongside`, `required` | `alongside`/`required` require `local-receipt-gate` in `mode`; `required` additionally requires `.github/workflows/verify-receipt.yml` to exist |
| `selfHostedLabels` | non-empty strings; must include `self-hosted` when `self-hosted-runner` is in `mode` | the labels the scaffolded workflow routes same-repo PRs to and `aesop runner install` registers |

The block is optional; every key has the default shown. Both readers validate it with the
same finding codes -- `tools/common.py` (`validate_ci_config`, `load_aesop_config`) for the
Python tools and `tools/ci_config.js` for `aesop doctor` / `bin/cli.js` -- and
`tests/test_ci_config.py` drives both on identical fixtures so they cannot drift. An invalid
block is a failed doctor check (`aesop.config.json (structure & required fields)`), e.g.
`CI_RECEIPT_GATE_REQUIRES_WORKFLOW` for `receiptGate: required` with no verify workflow.

## Scaffold: `aesop init --ci-mode`

```
aesop init --ci-mode hosted
aesop init --ci-mode self-hosted-runner --self-hosted-labels self-hosted,windows,aesop-box
aesop init --ci-mode local-receipt-gate
```

`--ci-mode` defaults to `hosted`. The mode is validated and every workflow is rendered
before anything is written, so a bad mode (exit 2) or a refused one (exit 1) leaves the
directory untouched. Templates live in `templates/ci/`:

- `ci-hosted.yml` -- today's `ci.yml` shape: PR-only trigger, per-branch concurrency, a
  sharded Linux job and a Windows job, gates kept step-level (a job-level skip or a `needs:`
  without `always()` makes a required check report `skipped`, which deadlocks branch
  protection).
- `ci-self-hosted-windows.yml` -- the same, with the Windows job's `runs-on` routing by PR
  origin: `github.event.pull_request.head.repo.fork == true` goes to `windows-latest`,
  everything else to `fromJSON('<your selfHostedLabels>')`. Rendered from the config/flag
  labels at scaffold time; the checkout uses `persist-credentials: false`.
- `verify-receipt.yml` -- a documented **placeholder** (marker line `aesop-template:
  PLACEHOLDER`). The scaffold prefers a real `.github/workflows/verify-receipt.yml` in the
  aesop checkout; when only the placeholder exists it refuses with a message naming the lane
  and writes nothing. When the receipt lane lands, replace the template body with that
  workflow verbatim and drop the marker.

Every template parses, and every rendered workflow passes the repo's own
`tools/ci_workflow_lint.py` and `tools/ci_needs_skip_guard.py` (`tests/test_ci_templates.py`
runs both against a scaffolded temp repo).

## Doctor: `aesop doctor` and the capability table

```
aesop doctor
aesop doctor --json
```

`aesop doctor` ends with a **report-only** `CI capability` section. It never fails the doctor:
a missing optional capability is a row, not an exit code. It probes:

- OS + version; CPU cores; RAM
- on Windows: Smart App Control (`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy`
  `VerifiedAndReputablePolicyState`: `0` off, `1` enforced, `2` evaluation) and user-mode code
  integrity (`Win32_DeviceGuard` `UsermodeCodeIntegrityPolicyEnforcementStatus`: `0` off,
  `1` audit, `2` enforced) -- either enforced means the unsigned GitHub Actions runner binaries
  are blocked, so `self-hosted-runner` is reported as not runnable with that reason
- WSL and Docker presence (running the Linux shards locally on Windows)
- `gh auth status` (the runner registration token is minted through `gh api`)
- `cloudflared` presence (the event bridge)
- whether the receipt tools are present in this checkout (feature-detected, see above)

and prints the table `mode -> runnable here: yes/no + why`. `--json` emits
`{checks, summary, ci_capability}` where `summary` counts only the pass/fail checks. The probe
is `tools/ci_capability.py`; set `AESOP_CI_PROBE_FIXTURE=<file.json>` to inject raw probe
results (tests and dry-runs) so no registry, PowerShell or `gh` call is made.

On a Windows developer box with Smart App Control enforced the table reads:

```
mode                   runnable here   why
hosted                 yes             runs on GitHub-hosted runners; nothing is required on this machine
self-hosted-runner     no              Smart App Control is enforced (VerifiedAndReputablePolicyState=1): unsigned runner binaries are blocked; ...
local-receipt-gate     no              pending: tools/emit_receipt.py, ... not present in this checkout (receipt lane feat/receipt-gate-increment-1 has not landed)
```

## Runner: `aesop runner install` / `aesop runner remove`

```
aesop runner install --instances 2 --labels self-hosted,windows,aesop-box --dry-run
aesop runner install --repo OWNER/NAME --set-fork-policy
aesop runner remove --instances 2 --dry-run
```

`install` runs the doctor's capability probe as preflight and **refuses** (exit 1) when:

- Smart App Control or UMCI is enforced on this Windows box (the same predicate as the doctor
  row, `tools/ci_capability.py` `runner_blocked`), with that message;
- `gh auth status` is not authenticated;
- the repository is public and `fork-pr-contributor-approval` is not
  `all_external_contributors` -- a self-hosted runner executes pull-request code on your
  hardware. `--set-fork-policy` sets it via
  `gh api -X PUT repos/<r>/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors`.

Then it follows GitHub's documented steps: resolve the latest `actions/runner` release via
`gh api`, pick the asset for this platform, take its sha256 from the release notes
(`<!-- BEGIN SHA <platform> -->`) and refuse any archive that does not match, mint a
registration token with `gh api -X POST repos/<r>/actions/runners/registration-token` (used
immediately, never printed -- plans show a placeholder), and run
`config.cmd|sh --unattended --runasservice --labels ...` once per instance in sibling
directories `<install-root>/runner-1..N`. `--dry-run` prints the plan and stops before any
download or registration; `--json` prints it as JSON. `remove` mints a remove token the same
way and runs `config remove` per instance. Labels default to the config's `selfHostedLabels`
default; `--repo` defaults to the `origin` remote.

The implementation is `tools/runner_install.py` (`bin/cli.js` dispatches to it with the
fail-closed exit-code propagator). Its tests inject both `gh` and the probe, so nothing is ever
installed by the suite.

## Where this lands in the merge pipeline

- Hosted is the default and always works.
- Pick `self-hosted-runner` when Windows minutes are the queue and you have a box the doctor
  reports as runnable (not a Smart-App-Control-enforced developer machine).
- Pick `local-receipt-gate` once the receipt lane has landed; until then the doctor shows it
  pending and the scaffold refuses, by design, rather than emitting a workflow that cannot run.
