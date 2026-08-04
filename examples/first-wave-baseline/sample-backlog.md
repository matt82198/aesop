# First Wave: Sample Backlog

This backlog demonstrates a realistic first wave with 5 file-disjoint, small-scope items suitable for parallel dispatch.

## Item 1: readme-typo-fix

**Slug:** readme-typo-fix  
**Owns:** README.md  
**Evidence:** README.md contains "orchestraion" (line ~12) instead of "orchestration"

**Description:**
Fix a single-character typo in the project README where "orchestration" is misspelled. This is a trivial fix that serves as a smoke test for the dispatch cycle—can a worker find a simple issue, fix it, and verify the change?

**Expected Effort:** < 2 minutes  
**Risk:** Minimal; single-file, one-line change  
**Test:** Grep for correct spelling; confirm typo is gone

---

## Item 2: enable-skipped-test

**Slug:** enable-skipped-test  
**Owns:** tests/test_example.js  
**Evidence:** tests/test_example.js line ~25 has `it.skip()`

**Description:**
A test in the suite is marked with `.skip`, which means it was temporarily disabled during development. This test validates core functionality and should be re-enabled. The worker must: remove the `.skip`, verify the test passes, and add a brief comment explaining why it was re-enabled (e.g., "ready for CI" or "dependency resolved").

**Expected Effort:** 3–5 minutes  
**Risk:** Low; validates existing functionality  
**Gate:** no `.skip` marker remains in tests/test_example.js AND `npm test -- tests/test_example.js` passes. Fails pre-work (the skip is still there), passes post-work.

---

## Item 3: add-eslint-config

**Slug:** add-eslint-config  
**Owns:** .eslintrc.json, package.json  
**Evidence:** Project has src/ and tests/ but no linting configuration

**Description:**
Set up ESLint for the project. Create a `.eslintrc.json` file with a sensible base configuration (e.g., airbnb or standard preset). Update `package.json` to add a `lint` script that runs eslint over `src/` and `tests/`. Ensure the configuration is valid and `npm run lint` executes without errors.

**Expected Effort:** 5–10 minutes  
**Risk:** Low; adds infrastructure, no business logic changes  
**Gate:** `.eslintrc.json` exists AND `package.json` declares a `lint` script AND `npm run lint` exits 0. It runs the real linter rather than grepping package.json for the string `lint`, which would already match `eslint` in devDependencies.

---

## Item 4: fix-doc-links

**Slug:** fix-doc-links  
**Owns:** docs/ARCHITECTURE.md, docs/SETUP.md  
**Evidence:** docs/ directory exists with broken internal references (e.g., `[Setup](./setup.md)` should be `[Setup](./SETUP.md)`)

**Description:**
Review the documentation files for broken links and incorrect cross-references. Fix any links that use wrong casing, point to non-existent files, or use incorrect paths. Ensure all `.md` files in docs/ follow the index structure and links are consistent.

**Expected Effort:** 5–15 minutes  
**Risk:** Very low; documentation-only, no code impact  
**Gate:** every relative markdown link in both files is resolved against the filesystem; unresolved targets are listed and the gate exits 1. A mere `test -f` on the two files would pass without anyone fixing a link.

---

## Item 5: simplify-util-functions

**Slug:** simplify-util-functions  
**Owns:** src/utils/helpers.js, src/utils/helpers.test.js  
**Evidence:** helpers.js contains duplicated utility logic (e.g., two nearly identical string-trimming or validation functions)

**Description:**
Refactor `src/utils/helpers.js` to reduce duplication or improve clarity. Examples:
- Consolidate two similar utility functions into one parameterized version
- Extract common logic into a shared helper
- Rename functions for better clarity (e.g., `validateEmail` → `isValidEmail`)

Update the corresponding test file (`src/utils/helpers.test.js`) to verify all refactored functions still pass. Include a docstring comment in the refactored code explaining the refactor goal (e.g., "Unified email and phone validation to reduce duplication").

**Expected Effort:** 10–20 minutes  
**Risk:** Low; existing tests validate correctness  
**Gate:** src/utils/helpers.js contains a `Refactor goal:` comment AND `npm test -- src/utils/helpers.test.js` passes. Honest limitation: this proves the file was touched and the tests are green; it cannot judge whether the refactor actually improved the code. That stays a human review call.

---

## Wave Characteristics

- **Total Items:** 5
- **Disjointness:** All file sets are non-overlapping (proven by `python tools/wave_manifest_lint.py` -> `PASS: ownership_disjointness`)
- **Gate honesty:** all 5 testCmds verified fail-closed by `verify-testcmds.sh` (fail pre-work, pass post-work)
- **Parallelizability:** All 5 can be worked on simultaneously by different workers
- **Complexity Spread:**
  - Trivial: Item 1 (typo)
  - Simple: Items 2, 3, 4
  - Moderate: Item 5 (refactoring)
- **Total Expected Time:** 30–60 minutes end-to-end
- **Merge Time:** ~5 minutes (5 independent PRs, no conflicts expected)

This is a deliberately small, low-risk wave suitable for:
1. **Onboarding new adopters** to see the full cycle
2. **CI/CD validation** without production risk
3. **Team training** on parallel dispatch and merge workflows
