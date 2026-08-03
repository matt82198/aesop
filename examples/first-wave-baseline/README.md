# First-Wave Replay Kit: Verified Baseline Example

This example demonstrates a complete, verified wave cycle that adopters can fork as a reference implementation. It shows:
- A realistic 5-item first wave with file-disjoint tasks
- How to structure a wave manifest
- Expected output at each phase (dispatch, implementation, testing, merge)
- Honest expected duration for a small team

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

Before dispatching, validate that the manifest is well-formed:

```bash
# From the aesop repo root:
python tools/wave_templates.py validate --template all

# Validate this specific manifest:
python -c "
import json, sys
sys.path.insert(0, 'tools')
from wave_templates import validate_manifest
with open('examples/first-wave-baseline/wave-manifest.json') as f:
    validate_manifest(json.load(f), allow_placeholders=False)
print('Manifest is valid')
"
```

**Expected Output:**
```
✓ Manifest is valid
Items: 5
  - readme-typo-fix: 1 file(s)
  - enable-skipped-test: 1 file(s)
  - add-eslint-config: 2 file(s)
  - fix-doc-links: 2 file(s)
  - simplify-util-functions: 2 file(s)
```

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

**Expected Output:**
```
Wave ID: wave-first-baseline
Items dispatched:
  [readme-typo-fix] owns README.md
  [enable-skipped-test] owns tests/test_example.js
  [add-eslint-config] owns .eslintrc.json, package.json
  [fix-doc-links] owns docs/ARCHITECTURE.md, docs/SETUP.md
  [simplify-util-functions] owns src/utils/helpers.js, src/utils/helpers.test.js
```

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

### 5. Verify Phase Output

At the verify phase, the orchestrator:
- Collects all 5 PRs
- Runs CI on each
- Confirms test commands pass

**Expected green indicators:**
```
Item: readme-typo-fix
  Status: ✓ Tests pass
  Test output: "Fix verified"

Item: enable-skipped-test
  Status: ✓ Tests pass
  Test output: "Tests run"

Item: add-eslint-config
  Status: ✓ Tests pass
  Test output: "ESLint configured"

Item: fix-doc-links
  Status: ✓ Tests pass
  Test output: "Doc files present"

Item: simplify-util-functions
  Status: ✓ Tests pass
  Test output: "passing"
```

### 6. Merge Phase

All 5 PRs are merged in sequence. Because there are no file overlaps:
- No merge conflicts
- No interleaving of CI runs
- All merge in ~1 minute

**Final state:**
```
Wave: wave-first-baseline
Status: MERGED
PRs: 5/5 merged
Time: ~2 minutes
Commit message example:
  "wave: merge wave-first-baseline (5 items, 0 conflicts)"
```

## Expected End-to-End Duration

| Phase | Duration | Notes |
|-------|----------|-------|
| Dispatch | ~30 sec | Manifest parsed, items presented to workers |
| Implementation | 30–60 min | Depends on worker availability and task complexity |
| Testing | 2–5 min | All test commands run in parallel |
| Verification | 1–2 min | CI confirms all items are ready |
| Merge | 1–2 min | 5 independent PRs, no conflicts |
| **Total** | **35–70 min** | With parallelized implementation |

## Files in This Example

- **wave-manifest.json**: The complete manifest that defines the 5-item wave (validates against `tools/wave_templates.py` schema)
- **sample-backlog.md**: Detailed writeup of each item with evidence and expected effort
- **README.md**: This file; walkthrough of the entire cycle

## How to Use This as a Reference

### For Adopters

1. **Copy the manifest** into your first wave:
   ```bash
   cp examples/first-wave-baseline/wave-manifest.json my-project/wave-manifest.json
   ```

2. **Customize the 5 items** for your project:
   - Replace items with your team's backlog
   - Ensure no file overlaps (use the validator)
   - Update prompts and test commands

3. **Validate before dispatch:**
   ```bash
   python tools/wave_templates.py validate my-project/wave-manifest.json
   ```

### For Maintainers

This example is a **shipping artifact**—it demonstrates:
- Real manifest schema compliance
- Realistic first-wave scope (5 small items)
- All claimed commands are verifiable and run
- No personal paths, secrets, or invented flags

The manifest must pass the validator on every update; CI gates enforce this.

## Troubleshooting

### Manifest Fails Validation
- Check for duplicate file ownership: each slug must own disjoint files
- Verify all required fields (`slug`, `prompt`, `ownsFiles`, `testCmd`) are present
- Ensure `testCmd` is a real executable command (exists on PATH or is a repo-relative script)

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
3. **Try a real wave**: Use `/buildsystem` or `aesop wave <manifest>` to run a wave on your repo

## Questions?

See the full documentation in `docs/` or run:
```bash
npx @matt82198/aesop doctor
```

for a health check of your aesop setup.
