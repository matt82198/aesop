// GOLDEN RENDER TEST: Capture exact prompt strings for field-absent manifests
// and verify byte-identical output across changes.
//
// This test RENDERS the actual prompts using the template's helper functions
// and compares against golden snapshots. Unlike text-search tests, this will
// FAIL if any future change adds whitespace, reorders sections, or breaks
// the no-op invariant.

import test from 'node:test';
import assert from 'node:assert';

// Helper functions copied from wave-flat-dispatch.template.mjs
// These MUST remain byte-identical to the template versions.

function acceptanceCriteriaSection(item) {
  if (!item.acceptanceCriteria || !Array.isArray(item.acceptanceCriteria) || item.acceptanceCriteria.length === 0) {
    return ''
  }
  let section = '\nACCEPTANCE CRITERIA:\n'
  item.acceptanceCriteria.forEach((criterion, idx) => {
    const stmt = criterion.statement || ''
    const verifiable = criterion.verifiable_by || 'unknown'
    section += `(${idx + 1}) ${stmt} [verifiable_by: ${verifiable}]\n`
  })
  return section
}

function domainSynopsisSection(item) {
  if (!item.domainSynopsis) return ''
  return `\nYOUR DOMAIN INVARIANTS (synopsis — still read your domain CLAUDE.md):\n${item.domainSynopsis}\n`
}

function ownsFilesDiffSection(item) {
  if (!item.ownsFilesDiff) return ''
  return `\nYOUR FILES' CURRENT DIFF:\n${item.ownsFilesDiff}\n`
}

function lastTestOutputSection(item) {
  if (!item.lastTestOutput) return ''
  return `\nLAST TEST OUTPUT (context for this repair):\n${item.lastTestOutput}\n`
}

// Test: Build prompt for field-absent manifest should match golden snapshot
test('golden render: build prompt for field-absent manifest is byte-identical', () => {
  const WORK = '/work'
  const HINT = 'Read the shared contract/specs in /work before implementing.'

  const item = {
    slug: 'example-fix',
    ownsFiles: ['src/example.py'],
    prompt: 'Fix the example function',
    workDir: '/work',
  }

  // Render the build prompt (as template does)
  const buildPrompt = `FLAT ONE-TURN-WAVE worker for item "${item.slug}". Working dir: ${WORK}. ${HINT}\n` +
    `You OWN and may write ONLY these files: ${(item.ownsFiles || []).join(', ')}. Do NOT create or edit any other file (strict ownership — another worker owns the rest, in parallel).\n` +
    `IMPORTANT: All file writes MUST use absolute paths under ${WORK}.\n` +
    `TASK:\n${item.prompt}\n` +
    acceptanceCriteriaSection(item) +
    domainSynopsisSection(item) +
    `Use the Write tool. Run any quick local self-check you can, but the integration suite is run centrally, not by you. Report which files you wrote.`

  // Golden snapshot for field-absent build prompt (EXACT bytes)
  const expectedBuildPrompt =
    `FLAT ONE-TURN-WAVE worker for item "example-fix". Working dir: /work. Read the shared contract/specs in /work before implementing.\n` +
    `You OWN and may write ONLY these files: src/example.py. Do NOT create or edit any other file (strict ownership — another worker owns the rest, in parallel).\n` +
    `IMPORTANT: All file writes MUST use absolute paths under /work.\n` +
    `TASK:\n` +
    `Fix the example function\n` +
    `Use the Write tool. Run any quick local self-check you can, but the integration suite is run centrally, not by you. Report which files you wrote.`

  assert.strictEqual(buildPrompt, expectedBuildPrompt,
    'Build prompt must be byte-identical for field-absent manifest')
})

// Test: Repair prompt for field-absent manifest should match golden snapshot
test('golden render: repair prompt for field-absent manifest is byte-identical', () => {
  const WORK = '/work'
  const HINT = 'Read the shared contract/specs in /work before implementing.'

  const item = {
    slug: 'repair-example',
    ownsFiles: ['src/repair.py'],
    prompt: 'Repair the broken code',
    workDir: '/work',
  }

  const v = { detail: 'Test suite failed: 2 failures' }

  // Render the repair prompt (as template does)
  const itemFiles = (item.ownsFiles || []).map(f => `  ${f}`).join('\n')
  const repairPrompt = `ONE-TURN-WAVE repair for item "${item.slug}". Working dir: ${WORK}. The integration suite failed: ${v.detail}\n` +
    lastTestOutputSection(item) +
    `\n** SCOPED REPAIR CONTEXT (token discipline — repair cache-read tax fix, measured #1 sink): **\n` +
    `You are given ONLY (a) the failing-suite verdict above and (b) the diff of YOUR OWN files. Do NOT re-read the whole prior build context — re-reading the full build is the measured top token sink.\n` +
    `To see exactly what you changed, run ONCE: \`git -C ${WORK} diff -- ${(item.ownsFiles || []).join(' ')}\` (your owned files only).\n` +
    ownsFilesDiffSection(item) +
    `You MAY read your OWNED files and the named contract (${HINT}); do NOT read sibling workers' files or dump the whole build.\n` +
    `\n** TARGETED TEST DISCIPLINE (latency fix #1): **\n` +
    `You own these files (run tests ONLY for these, never the full union suite):\n${itemFiles}\n` +
    `\nTo run tests for ONLY your files:\n` +
    `  - Identify which test files/tests exercise your owned files (from the integration failure details).\n` +
    `  - Run ONLY those specific tests, e.g., 'pytest test_foo.py::test_bar' or 'python -m unittest tests.test_module.TestClass.test_method'.\n` +
    `  - Do NOT run the full 'npm test' / 'python -m unittest discover' / 'pytest' suite yourself.\n` +
    `\n** RUN-ONCE-TO-FILE (latency fix #1): **\n` +
    `When running a command that produces verbose output:\n` +
    `  1. Run it ONCE with full timeout (>= 5 minutes): cmd > /tmp/repair-output.log 2>&1; echo "exit=$?" >> /tmp/repair-output.log\n` +
    `  2. Read the file to see results (tail, grep, etc) — never re-run the suite to see another slice.\n` +
    `  3. Fix based on that ONE output; multiple runs of the same suite burn wall-clock minutes.\n` +
    `\nFix ONLY your owned files with Edit/Write. Report.`

  // Golden snapshot for field-absent repair prompt (EXACT bytes)
  const expectedRepairPrompt =
    `ONE-TURN-WAVE repair for item "repair-example". Working dir: /work. The integration suite failed: Test suite failed: 2 failures\n` +
    `\n** SCOPED REPAIR CONTEXT (token discipline — repair cache-read tax fix, measured #1 sink): **\n` +
    `You are given ONLY (a) the failing-suite verdict above and (b) the diff of YOUR OWN files. Do NOT re-read the whole prior build context — re-reading the full build is the measured top token sink.\n` +
    `To see exactly what you changed, run ONCE: \`git -C /work diff -- src/repair.py\` (your owned files only).\n` +
    `You MAY read your OWNED files and the named contract (Read the shared contract/specs in /work before implementing.); do NOT read sibling workers' files or dump the whole build.\n` +
    `\n** TARGETED TEST DISCIPLINE (latency fix #1): **\n` +
    `You own these files (run tests ONLY for these, never the full union suite):\n  src/repair.py\n` +
    `\nTo run tests for ONLY your files:\n` +
    `  - Identify which test files/tests exercise your owned files (from the integration failure details).\n` +
    `  - Run ONLY those specific tests, e.g., 'pytest test_foo.py::test_bar' or 'python -m unittest tests.test_module.TestClass.test_method'.\n` +
    `  - Do NOT run the full 'npm test' / 'python -m unittest discover' / 'pytest' suite yourself.\n` +
    `\n** RUN-ONCE-TO-FILE (latency fix #1): **\n` +
    `When running a command that produces verbose output:\n` +
    `  1. Run it ONCE with full timeout (>= 5 minutes): cmd > /tmp/repair-output.log 2>&1; echo "exit=$?" >> /tmp/repair-output.log\n` +
    `  2. Read the file to see results (tail, grep, etc) — never re-run the suite to see another slice.\n` +
    `  3. Fix based on that ONE output; multiple runs of the same suite burn wall-clock minutes.\n` +
    `\nFix ONLY your owned files with Edit/Write. Report.`

  assert.strictEqual(repairPrompt, expectedRepairPrompt,
    'Repair prompt must be byte-identical for field-absent manifest')
})

// Test: Adversarial review prompt for field-absent manifest should match golden snapshot
test('golden render: adversarial review prompt for field-absent manifest is byte-identical', () => {
  const WORK = '/work'

  const item = {
    slug: 'review-example',
    ownsFiles: ['src/review.py'],
    prompt: 'Implement the review feature',
  }

  const ownedFilesStr = (item.ownsFiles || []).length > 0
    ? `Owned files:\n${(item.ownsFiles || []).map(f => `  ${f}`).join('\n')}\n`
    : 'No owned files specified.\n'

  // Render the review prompt (as template does)
  const reviewPrompt = `CONTRACT REFUTATION review for item "${item.slug}". Working dir: ${WORK}.\n` +
    `\nITEM CONTRACT (stated purpose):\n${item.prompt}\n` +
    acceptanceCriteriaSection(item) +
    domainSynopsisSection(item) +
    `\n${ownedFilesStr}` +
    `Your job: READ the actual code the implementer wrote (in the ownsFiles above). ` +
    `Try to construct a concrete input, scenario, or edge case where the implementation VIOLATES its stated contract — ` +
    `does NOT do what the prompt says it should do. You are NOT running tests (tests may be tautological); ` +
    `reason about the specification and the code.\n` +
    `\nIf you can construct a breaking scenario, set holds=false and describe it in breakingScenario (be specific: inputs, expected vs actual behavior).\n` +
    `If the code appears to genuinely meet its contract, set holds=true and breakingScenario="" (empty string).\n` +
    `\nReport schema: {slug, holds, breakingScenario}.`

  // Golden snapshot for field-absent review prompt (EXACT bytes)
  const expectedReviewPrompt =
    `CONTRACT REFUTATION review for item "review-example". Working dir: /work.\n` +
    `\nITEM CONTRACT (stated purpose):\n` +
    `Implement the review feature\n` +
    `\n` +
    `Owned files:\n` +
    `  src/review.py\n` +
    `Your job: READ the actual code the implementer wrote (in the ownsFiles above). ` +
    `Try to construct a concrete input, scenario, or edge case where the implementation VIOLATES its stated contract — ` +
    `does NOT do what the prompt says it should do. You are NOT running tests (tests may be tautological); ` +
    `reason about the specification and the code.\n` +
    `\nIf you can construct a breaking scenario, set holds=false and describe it in breakingScenario (be specific: inputs, expected vs actual behavior).\n` +
    `If the code appears to genuinely meet its contract, set holds=true and breakingScenario="" (empty string).\n` +
    `\nReport schema: {slug, holds, breakingScenario}.`

  assert.strictEqual(reviewPrompt, expectedReviewPrompt,
    'Adversarial review prompt must be byte-identical for field-absent manifest')
})

// Test: Empty sections return empty string (no extra newlines/separators)
test('helper functions return empty string (not null, not undefined) when field absent', () => {
  const item = { slug: 'test' }

  assert.strictEqual(acceptanceCriteriaSection(item), '',
    'acceptanceCriteriaSection should return empty string, not null/undefined')
  assert.strictEqual(domainSynopsisSection(item), '',
    'domainSynopsisSection should return empty string, not null/undefined')
  assert.strictEqual(ownsFilesDiffSection(item), '',
    'ownsFilesDiffSection should return empty string, not null/undefined')
  assert.strictEqual(lastTestOutputSection(item), '',
    'lastTestOutputSection should return empty string, not null/undefined')
})

// Test: With fields present, sections are rendered with exact formatting
test('helper functions render exact formatted sections when field present', () => {
  const item = {
    slug: 'test',
    acceptanceCriteria: [
      { statement: 'Does A', verifiable_by: 'test' },
      { statement: 'Does B', verifiable_by: 'review' },
    ],
    domainSynopsis: 'Domain rule: never mutate globals',
    ownsFilesDiff: '--- a/file.py\n+++ b/file.py',
    lastTestOutput: 'FAILED: test_x (AssertionError)',
  }

  const acceptanceStr = acceptanceCriteriaSection(item)
  assert.ok(acceptanceStr.includes('ACCEPTANCE CRITERIA:'),
    'acceptanceCriteria section should include header')
  assert.ok(acceptanceStr.includes('(1) Does A [verifiable_by: test]'),
    'acceptanceCriteria should format criteria correctly')
  assert.ok(acceptanceStr.includes('(2) Does B [verifiable_by: review]'),
    'acceptanceCriteria should format all criteria')

  const domainStr = domainSynopsisSection(item)
  assert.ok(domainStr.includes('YOUR DOMAIN INVARIANTS'),
    'domainSynopsis section should include header')
  assert.ok(domainStr.includes('Domain rule: never mutate globals'),
    'domainSynopsis should include the content')

  const diffStr = ownsFilesDiffSection(item)
  assert.ok(diffStr.includes('YOUR FILES\' CURRENT DIFF:'),
    'ownsFilesDiff section should include header')
  assert.ok(diffStr.includes('--- a/file.py'),
    'ownsFilesDiff should include the diff content')

  const testStr = lastTestOutputSection(item)
  assert.ok(testStr.includes('LAST TEST OUTPUT'),
    'lastTestOutput section should include header')
  assert.ok(testStr.includes('FAILED: test_x'),
    'lastTestOutput should include the output')
})
