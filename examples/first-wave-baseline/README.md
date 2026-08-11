# First-Wave Replay Kit: Verified Baseline Example

This example demonstrates a complete, verified wave cycle that adopters can fork as a reference implementation. It shows:
- A realistic 5-item first wave with file-disjoint tasks
- How to structure a wave manifest
- Real, captured command output for every verifiable step (nothing in this file is hand-written
  sample output; anything not captured from a run is labelled as an estimate)
- Test commands that are verifiably **fail-closed** — they fail when the work has not been done

## What This Demonstrates

1. **Manifest Design**: A realistic wave-manifest.json with 5 small, independent items
2. **Parallel Dispatch**: All items can be worked on simultaneously without conflicts
3. **CI/Test Integration**: Each item includes realistic test commands
4. **Merge Workflow**: How to verify and merge the wave without manual intervention

## Prerequisites

- Node.js 18+ and npm
- Python 3.8+ with `aesop` in PATH (or via `pip install -e .`)
- Git 2.35+ with signing configured
- A test repository to fork (see "Setup" below)

## Quick Start

### 1. Create a Test Repository

```bash
# Clone a minimal test repo or create one:
git clone https://github.com/some-org/simple-repo.git my-test-wave
cd my-test-wave
git checkout -b wave/first-baseline
```

### 2. Verify the Manifest

**The command to use is `tools/wave_manifest_lint.py <path>`.** `tools/wave_templates.py validate`
only validates the *built-in presets* (`--template saas|data|library|all`) — it takes no file
argument and will exit 2 with `unrecognized arguments` if you hand it a manifest path.

```bash
# From the aesop repo root — lint this manifest file:
python tools/wave_manifest_lint.py examples/first-wave-baseline/wave-manifest.json
```

Real output (captured 2026-08-02):

```
PASS: ownership_disjointness: No file ownership overlaps
INFO: path_existence: 5 new file(s)
PASS: prompt_sanity: All prompts valid
PASS: git_history_churn: No high-churn files detected
WARN: testcmd_validity: No testCmd specified
```

Exit code `0`. Two honest notes about that output:

- **The `testcmd_validity` WARN is a linter quirk, not a manifest defect.** That check reads a
  *top-level* `manifest["testCmd"]` key; this kit (like the wave engine) puts `testCmd` on each
  item, so the check finds nothing to inspect. Every item does have a `testCmd` — see §7.
- **`--strict` exits 1** on this manifest, because `--strict` promotes any WARN (including the
  one above) to a failure. Use plain (non-strict) mode for this kit.

The same lint is reachable through the CLI as `node bin/cli.js wave manifest-lint <path>`
(or `npx @matt82198/aesop wave manifest-lint <path>`), which produces byte-identical output.

If you want the schema check that the wave engine itself performs, call `validate_manifest`
directly:

```bash
python -c "
import json, sys
sys.path.insert(0, 'tools')
from wave_templates import validate_manifest
with open('examples/first-wave-baseline/wave-manifest.json') as f:
    validate_manifest(json.load(f), allow_placeholders=False)
print('Manifest is valid')
"
```

Real output — a single line, exit code `0`:

```
Manifest is valid
```

`validate_manifest` raises on failure and returns `None` on success; it prints no item counts or
per-slug summaries. If you want those, see the inspect step below.

To validate the *built-in presets* (not this file), the correct invocation is:

```bash
python tools/wave_templates.py validate --template all
```

Real output:

```
✓ saas: valid
✓ data: valid
✓ library: valid

All 3 preset(s) validated successfully.
```

(On a Windows console that is not running UTF-8, each checkmark above renders as its literal
six-character backslash-u escape sequence instead of a tick. That is a console encoding artifact,
not a failure -- set `PYTHONIOENCODING=utf-8` for real checkmarks; the exit code is `0` either way.)

### 3. Dispatch the Wave

The wave is dispatched by the orchestrator (via the built-in wave engine). For this walkthrough, simulate the dispatch by reading the manifest and presenting items to workers:

```bash
# From the aesop repo root:
node -e "
const fs = require('fs');
const manifest = JSON.parse(fs.readFileSync('examples/first-wave-baseline/wave-manifest.json', 'utf8'));
console.log('Wave ID:', manifest.wave_id);
console.log('Items dispatched:');
manifest.items.forEach(item => {
  console.log(\`  [\${item.slug}] owns \${item.ownsFiles.join(', ')}\`);
});
"
```

Real output (captured 2026-08-02, exit code `0`):

```
Wave ID: wave-first-baseline
Items dispatched:
  [readme-typo-fix] owns README.md
  [enable-skipped-test] owns tests/test_example.js
  [add-eslint-config] owns .eslintrc.json, package.json
  [fix-doc-links] owns docs/ARCHITECTURE.md, docs/SETUP.md
  [simplify-util-functions] owns src/utils/helpers.js, src/utils/helpers.test.js
```

**Key observation:** the five `ownsFiles` sets are disjoint, which is what makes parallel dispatch
and conflict-free sequential merge possible. `wave_manifest_lint.py` proves this mechanically —
that is the `PASS: ownership_disjointness` line in step 2.

### 4. Implement Items (Simulated)

Each worker would be presented with their item's prompt and expected test command. For example, worker 1 receives:

**Prompt:** Fix the typo in README.md where 'orchestration' is misspelled as 'orchestraion'. Verify the file has exactly one instance and replace it. This is a simple one-line fix to demonstrate a trivial dispatch cycle.

**Test Command:** `grep -q 'orchestration' README.md && ! grep -q 'orchestraion' README.md && echo 'Fix verified'`

In a real wave, workers would:
1. Create a feature branch (e.g., `wave/first-baseline/readme-typo-fix`)
2. Make changes to owned files
3. Run the test command locally
4. Commit and push
5. Open a PR

### 5. Verify Phase — and how we know the gates are real

At the verify phase the orchestrator collects the branches, runs CI, and requires each item's
`testCmd` to exit `0`.

That is only meaningful if a `testCmd` **fails when the work has not been done**. A gate like
`test -f docs/ARCHITECTURE.md && echo 'Doc files present'` passes whether or not anybody touched
the links — it is fail-open, and it will happily bless an empty PR.

So rather than print an illustrative "all green" block here, this kit ships an executable proof:

```bash
bash examples/first-wave-baseline/verify-testcmds.sh
```

It builds a throwaway fixture repo in the pre-work state, runs each item's `testCmd` **verbatim
out of `wave-manifest.json`**, applies the five fixes, and runs them all again. Real output
(captured 2026-08-02, exit code `0`):

```
########## PRE-WORK: nothing implemented -- every gate must FAIL ##########
  readme-typo-fix            exit=1   OK   (fails as required)
  enable-skipped-test        exit=1   OK   (fails as required)
  add-eslint-config          exit=1   OK   (fails as required)
  fix-doc-links              exit=1   OK   (fails as required)
  simplify-util-functions    exit=1   OK   (fails as required)

########## POST-WORK: all 5 items implemented -- every gate must PASS ##########
  readme-typo-fix            exit=0   OK   (passes as required)
  enable-skipped-test        exit=0   OK   (passes as required)
  add-eslint-config          exit=0   OK   (passes as required)
  fix-doc-links              exit=0   OK   (passes as required)
  simplify-util-functions    exit=0   OK   (passes as required)

RESULT: all 5 testCmds are fail-closed.
```

The script needs `node` 18+, `npm`, `python`, and network access (it installs `eslint@8` so the
`add-eslint-config` gate can run the linter for real instead of grepping for the string `lint`).

### 6. Merge Phase

Because the five `ownsFiles` sets are disjoint, the branches merge in sequence with no conflicts.

The wave engine's own merge behaviour is in `driver/wave_loop.py`; this kit does not ship a
captured merge transcript, because the output depends on your remote, your branch protection
rules, and your CI. Do not treat any timing below as a measurement of your repo.

## Expected End-to-End Duration

These are **planning estimates, not measurements** — nobody timed a real run to produce this
table. Implementation time in particular is dominated by your model, your repo size, and your CI,
so treat it as an order of magnitude only.

| Phase | Estimate | Notes |
|-------|----------|-------|
| Dispatch | seconds | Manifest parsed, items handed to workers |
| Implementation | tens of minutes | Parallel across the 5 items; the dominant term |
| Testing | minutes | The 5 `testCmd`s; see §5 for what they actually assert |
| Verification | minutes | CI on each branch |
| Merge | minutes | 5 independent branches, no file overlap |

## Files in This Example

- **wave-manifest.json**: The 5-item wave definition. Lints clean under
  `python tools/wave_manifest_lint.py` and passes `wave_templates.validate_manifest`.
- **sample-backlog.md**: Per-item writeup with evidence and expected effort
- **verify-testcmds.sh**: Executable proof that every item's `testCmd` is fail-closed (see §5)
- **README.md**: This file; walkthrough of the entire cycle

## How to Use This as a Reference

### For Adopters

1. **Copy the manifest** into your first wave:
   ```bash
   cp examples/first-wave-baseline/wave-manifest.json my-project/wave-manifest.json
   ```

2. **Customize the 5 items** for your project:
   - Replace items with your team's backlog
   - Ensure no file overlaps (the linter's `ownership_disjointness` check proves this)
   - Update prompts and test commands

3. **Validate before dispatch** — note this is `wave_manifest_lint.py`, **not**
   `wave_templates.py validate`, which takes `--template` and no file argument:
   ```bash
   python tools/wave_manifest_lint.py my-project/wave-manifest.json
   ```

4. **Check your own testCmds are fail-closed.** Adapt `verify-testcmds.sh`: put your repo in the
   pre-work state, run each `testCmd`, and confirm every one exits non-zero *before* the work is
   done. Any gate that already passes is measuring nothing.

### For Maintainers

This example is a **shipping artifact**. What is actually verified, and how:

| Claim | How it is checked |
|-------|-------------------|
| Manifest schema is valid | `wave_templates.validate_manifest(..., allow_placeholders=False)` |
| File ownership is disjoint | `wave_manifest_lint.py` -> `PASS: ownership_disjointness` |
| Prompts carry the isolation marker | `wave_manifest_lint.py` -> `PASS: prompt_sanity` |
| Every `testCmd` is fail-closed | `verify-testcmds.sh` (pre-work FAIL / post-work PASS) |
| Command output in this README | Captured from real runs on 2026-08-02, not hand-written |

Known limitation, stated plainly: `simplify-util-functions` gates on the required
`Refactor goal:` marker plus a green test run. That proves the worker touched the file and left
the tests passing; it cannot judge whether the refactor genuinely improved the code. That last
step remains a human review call, and no `testCmd` in this kit pretends otherwise.

## Troubleshooting

### `wave_templates.py validate <path>` exits 2
That is expected — `validate` only takes `--template {saas,data,library,all}` and accepts no file
argument. To validate a manifest *file*, use `python tools/wave_manifest_lint.py <path>`.

### Manifest Fails Validation
- Check for duplicate file ownership: each slug must own disjoint files
- Verify all required fields (`slug`, `prompt`, `ownsFiles`, `testCmd`) are present
- Ensure `testCmd` is a real executable command (exists on PATH or is a repo-relative script)
- A `WARN: testcmd_validity: No testCmd specified` is expected for this kit: that check reads a
  top-level `testCmd`, while the wave engine reads a per-item `testCmd`. Do not "fix" it by
  adding a top-level key.

### Test Command Fails Locally
- The test commands assume a basic project structure (README.md, tests/, docs/, src/utils/)
- Customize test commands to match your actual project layout
- Use relative paths from the repository root

### Merge Conflicts Despite Disjoint Files
- Verify the manifest by running the validator
- Check for glob patterns in `ownsFiles` that might overlap
- Confirm all 5 branches are up-to-date with main before merging

## Next Steps

Once you've walked through this example:

1. **Read the architecture guide**: `docs/ARCHITECTURE.md` explains the wave engine
2. **Explore the driver**: `driver/wave_loop.py` shows the actual dispatch and merge logic
3. **Try a real wave**: use the `/buildsystem` skill, or drive the engine directly:
   ```bash
   python driver/wave_loop.py --manifest examples/first-wave-baseline/wave-manifest.json --one-turn
   ```
   (`aesop wave` is a namespace, not a runner — its verbs are `preflight`, `manifest-lint`,
   `template`, `scorecard`, and `resume`. `aesop wave <manifest>` exits 2 with
   `Unknown verb`.)

## Questions?

See the full documentation in `docs/` or run:
```bash
npx @matt82198/aesop doctor
```

for a health check of your aesop setup.
