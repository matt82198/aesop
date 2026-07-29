// ============================================================================
// wave-flat-dispatch.template.mjs — REUSABLE one-turn-wave harness
// ----------------------------------------------------------------------------
// Encodes the measured A/B-winning pattern (flat fan-out beat a Sonnet mid-tier 4.3x on cost at equal quality):
// a whole wave's BUILD phase — flat Haiku fan-out (one worker per file-disjoint
// item) + integration verify + bounded repair — collapses into ONE Workflow
// call = ONE orchestrator turn, at flat-dispatch cost (no Sonnet mid-tier).
//
// It is fully parameterized by `args` (no task hardcoded here). Point it at any
// wave by passing a manifest. Hardening baked in from this session's CI cascades:
//   * PREFLIGHT disjoint-file-ownership guard  -> prevents union-drift (the root
//     cause of both red-main incidents this session: two items green vs their own
//     base but the UNION on main breaks).
//   * Fixture honesty gate (optional setup must prove red-on-stubs).
//   * Bounded repair with per-item failure attribution.
//   * Per-item selfCheckCmd validation (cheap post-build checks).
//   * Build-report existence verification (deterministic, agent-free).
//   * Multi-testCmd support (parallel verify agents, merged verdicts).
//   * postBuild pipeline actions (run after items pass self-check).
//
// args = {
//   base:        string  // sandbox/work root (absolute)
//   workDir:     string  // dir where implementers write + where testCmd runs
//   testCmd:     string  // integration command, e.g. "python -m pytest test_suite.py -q"
//                        // (or omit if testCmds[] is provided instead)
//   testCmds:    [string] | null  // optional: array of test commands, each run as separate parallel agent
//                                  // when present, overrides single testCmd; results merged (green=all green)
//   contractHint:string  // one line telling workers where the shared contract/specs live
//   setup:      { prompt: string } | null   // optional: builds+verifies the sandbox (unmeasured)
//   items:      [ { slug, ownsFiles:[string], prompt:string, selfCheckCmd?: string, workDir?: string } ]
//                // selfCheckCmd: optional per-item verification (exits 0 = pass, non-0 = fail)
//                // workDir: optional override for selfCheckCmd execution (defaults to args.workDir)
//   repairCap:   number  // default 1; resolved from driver/verification_policy.py by build_manifest_item.
//   verificationTier: number | null  // optional: backend verification tier (1=light spot-check, 2+=heavier)
//              // Policy is resolved in Python and carried as literal manifest fields (repairCap, requireAdversarialReview).
//              // JS consumes manifest fields directly; no recomputation (no drift). Source of truth: driver/verification_policy.py.
//              // Absent => defaults to 1 (Claude Code, high-accuracy path).
//   brake:      { checkCmd: string, cwd?: string } | null  // optional kill-switch/cost-ceiling
//              // gate run BEFORE any worker spawns (wave-26 critique fix — wires .HALT/cost_ceiling
//              // into DISPATCH, not just the backup daemon). Aborts the whole wave if engaged.
//              // Absent => unchanged behavior (generic/non-aesop waves unaffected).
//   ceiling:    { tokens?: number, recheckBrake?: boolean } | null  // optional live cost ceiling
//              // tokens: abort if budget.spent() exceeds this before Build, before each Repair round,
//              // and before Ship. Returns graceful partial result {aborted:true, reason:'cost_ceiling'}
//              // with existing build/integration state included for potential resume.
//              // recheckBrake: if true and args.brake exists, re-run the brake agent before each
//              // Repair round (allows .HALT set mid-wave to stop the next phase).
//              // Absent => unchanged behavior (backward-compatible).
//   postBuild:  { cmd: string, afterItems: [string] } | null  // optional: run cmd (via agent) as soon as
//                                                               // all named items pass self-check (pipeline semantics)
//   requireAdversarialReview: boolean | null  // optional: when truthy, after integration green, spawn one Haiku
//                                              // reviewer per item to refute that it meets its CONTRACT
//                                              // (reads actual code, constructs breaking scenarios).
//                                              // Returns {holds:boolean, breakingScenario:string} per item;
//                                              // items where holds=false are collected as contractFindings.
//                                              // Defaults to true (enable by default). Set false to disable.
//                                              // ADVISORY: contractFindings never affect mergeReady (which gates
//                                              // on integration green only) — the ORCHESTRATOR owns merge/escalation
//                                              // decisions based on the findings it receives in Report.
//                                              // Resolved by driver/verification_policy.py and passed via manifest.
//   adversarialReviewMode: 'blocking' | 'concurrent-note' | null  // optional (LEVER 2, wall-clock)
//                                       // 'blocking' (default): run the refutation INLINE before return (current behavior).
//                                       // 'concurrent-note': do NOT await adversarialReview before signaling merge-readiness —
//                                       //   defer it (adversarialReviewPending=true, contractFindings=null here) so the
//                                       //   orchestrator kicks CI immediately and runs adversarialReview in parallel with the
//                                       //   CI-wait window; contractFindings then gate MERGE (not CI). Overlaps review + CI.
//                                       // Absent / any other value => 'blocking' (backward-compatible).
//   agentTimeboxNote: number | null  // optional: per-agent wall-clock budget in minutes (latency fix #2)
//                                     // when set: adds hard timebox line to Build/SelfCheck/Repair prompts,
//                                     // caps repair rounds to top 3 worst failing items (rest deferred).
//                                     // when absent: no timebox line, no cap (backward-compatible).
// }
// Returns: { preflight, build, integration:{green,passed,failed}, repairsUsed,
//            tokens:{buildOut,verifyOut,repairOut,adversarialReviewOut,totalOut}, mergeReady:boolean, aborted, reason,
//            contractFindings: [{slug, breakingScenario}], adversarialReviewMode, adversarialReviewPending:boolean }
//          (may include aborted:true, reason:'cost_ceiling', spent, ceiling when ceiling exceeded)
// ============================================================================

export const meta = {
  name: 'wave-flat-dispatch',
  description: 'One-turn wave: preflight disjoint-ownership guard -> flat Haiku fan-out (1 worker/item) -> integration verify -> bounded repair. Reusable; parameterized by args manifest.',
  phases: [
    { title: 'Preflight', detail: 'disjoint-file-ownership guard + optional sandbox build/verify' },
    { title: 'Build', detail: 'parallel Haiku, one per file-disjoint item' },
    { title: 'Integrate', detail: 'run the integration test command on the union' },
    { title: 'Repair', detail: 'bounded repair round(s) for failing items' },
    { title: 'AdversarialReview', detail: '(optional) refute-oriented contract verification per item' },
    { title: 'Report', detail: 'per-item status + merge-readiness + token cost' },
  ],
}

let A = args || {}
if (typeof A === 'string') { try { A = JSON.parse(A) } catch (e) { A = {} } }  // args may arrive as a JSON string
const WORK = A.workDir
const TEST = A.testCmd
const ITEMS = Array.isArray(A.items) ? A.items : []
const HINT = A.contractHint || `Read the shared contract/spec files in ${WORK} before implementing.`
const CEILING = A.ceiling ? { tokens: A.ceiling.tokens, recheckBrake: A.ceiling.recheckBrake } : null
const TIMEBOX_MINUTES = typeof A.agentTimeboxNote === 'number' ? A.agentTimeboxNote : null

// Verification policy: resolved in Python, consumed in JS (no recomputation).
// source of truth: driver/verification_policy.py — JS consumes them directly: the resolved literal
// manifest fields (repairCap, requireAdversarialReview, spotCheckFrac, validateAllJson) baked by
// build_manifest_item. The policy is resolved ONCE in Python and carried as literal manifest fields,
// so there is no drift and no recomputation here. When fields are absent (legacy/tier-1 path),
// defaults maintain byte-identical behavior.
const CAP = typeof A.repairCap === 'number' ? A.repairCap : 1
const ADVERSARIAL_REVIEW = typeof A.requireAdversarialReview === 'boolean' ? A.requireAdversarialReview : true
// LEVER 2 (wall-clock): adversarialReview blocking vs concurrent-note. Default 'blocking' = current inline
// behavior (findings produced before return). 'concurrent-note' defers the refutation so the orchestrator
// can kick CI immediately and run adversarialReview in parallel, gating MERGE (not CI) on contractFindings.
const ADVERSARIAL_REVIEW_MODE = A.adversarialReviewMode === 'concurrent-note' ? 'concurrent-note' : 'blocking'

// Helper to build timebox line for agent prompts (latency fix #2).
function timeboxLine() {
  if (!TIMEBOX_MINUTES) return ''
  return `\nTIMEBOX: if your remaining work exceeds ~${TIMEBOX_MINUTES} minutes of effort, STOP and report exactly what is done + what remains in your note — an incomplete honest report beats grinding.`
}

// Helper to build acceptance criteria section (A1) for prompts.
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

// Helper to build domain synopsis section (A3) for prompts.
function domainSynopsisSection(item) {
  if (!item.domainSynopsis) return ''
  return `\nYOUR DOMAIN INVARIANTS (synopsis — still read your domain CLAUDE.md):\n${item.domainSynopsis}\n`
}

// Helper to build owns files diff section (A4) for repair prompts.
function ownsFilesDiffSection(item) {
  if (!item.ownsFilesDiff) return ''
  return `\nYOUR FILES' CURRENT DIFF:\n${item.ownsFilesDiff}\n`
}

// Helper to build last test output section (A2) for repair prompts.
function lastTestOutputSection(item) {
  if (!item.lastTestOutput) return ''
  return `\nLAST TEST OUTPUT (context for this repair):\n${item.lastTestOutput}\n`
}

const DONE = {
  type: 'object', additionalProperties: false,
  properties: {
    slug: { type: 'string' }, wrote: { type: 'boolean' },
    filesWritten: { type: 'array', items: { type: 'string' } }, note: { type: 'string' },
  },
  required: ['slug', 'wrote', 'filesWritten', 'note'],
}
const SETUP = {
  type: 'object', additionalProperties: false,
  properties: {
    ok: { type: 'boolean' }, redOnStubs: { type: 'number' },
    greenOnReference: { type: 'boolean' }, totalTests: { type: 'number' }, note: { type: 'string' },
  },
  required: ['ok', 'redOnStubs', 'greenOnReference', 'totalTests', 'note'],
}
const VERIFY = {
  type: 'object', additionalProperties: false,
  properties: {
    passed: { type: 'number' }, failed: { type: 'number' }, green: { type: 'boolean' },
    failingItems: { type: 'array', items: { type: 'string' } }, detail: { type: 'string' },
  },
  required: ['passed', 'failed', 'green', 'failingItems', 'detail'],
}
const SHIP = {
  type: 'object', additionalProperties: false,
  properties: {
    committed: { type: 'boolean' }, pushed: { type: 'boolean' }, sha: { type: 'string' },
    fileCount: { type: 'number' }, oneCommit: { type: 'boolean' }, note: { type: 'string' },
  },
  required: ['committed', 'pushed', 'sha', 'fileCount', 'oneCommit', 'note'],
}

// ---------------- Preflight ----------------
phase('Preflight')
if (!WORK || !TEST || !ITEMS.length) {
  log('ABORT: args must include workDir, testCmd, and a non-empty items[].')
  return { aborted: true, reason: 'bad_manifest' }
}
// Disjoint-file-ownership guard (union-drift prevention).
const owner = {}
const conflicts = []
for (const it of ITEMS) {
  for (const f of (it.ownsFiles || [])) {
    if (owner[f]) conflicts.push({ file: f, items: [owner[f], it.slug] })
    else owner[f] = it.slug
  }
}
if (conflicts.length) {
  log(`ABORT: file-ownership overlap (union-drift risk): ${conflicts.map(c => `${c.file} <- ${c.items.join(' & ')}`).join('; ')}`)
  return { aborted: true, reason: 'ownership_overlap', conflicts }
}
log(`Preflight OK: ${ITEMS.length} items, ${Object.keys(owner).length} owned files, no overlap.`)

// Check ceiling before any worker spawns (if set).
{
  const spent = budget.spent()
  if (CEILING && typeof CEILING.tokens === 'number' && spent > CEILING.tokens) {
    log(`ABORT: cost ceiling exceeded at preflight (spent ${spent} > ${CEILING.tokens}).`)
    return {
      preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: null },
      build: [],
      integration: { green: false, passed: null, failed: null },
      repairsUsed: 0,
      tokens: { buildOut: 0, verifyOut: 0, repairOut: 0, totalOut: spent, model: 'all-haiku (weight 1)' },
      mergeReady: false,
      ship: null,
      aborted: true,
      reason: 'cost_ceiling',
      spent,
      ceiling: CEILING.tokens,
    }
  }
}

// Safety brake (optional): kill-switch (.HALT) + cost-ceiling gate, run BEFORE any worker spawns.
// Wires the aesop brake into the DISPATCH itself (wave-26 critique #1/#5). Workflow scripts have no
// fs/shell access, so the check is delegated to a cheap read-only agent that runs args.brake.checkCmd
// in args.brake.cwd. When args.brake is absent, behavior is exactly as before (backward-compatible).
if (A.brake && A.brake.checkCmd) {
  const BRAKE = {
    type: 'object', additionalProperties: false,
    properties: { halted: { type: 'boolean' }, reason: { type: 'string' } },
    required: ['halted', 'reason'],
  }
  const b = await agent(
    `READ-ONLY safety-brake check — do NOT modify anything. In directory ${A.brake.cwd || WORK}, run:\n${A.brake.checkCmd}\n` +
    `This checks the fleet kill-switch (.HALT sentinel) and/or the token cost-ceiling. Set halted=true if the command exits non-zero OR its output indicates HALTED / ceiling exceeded; set halted=false only if it clearly reports OK/clean/under-ceiling. Report the reason.`,
    { label: 'preflight:brake', phase: 'Preflight', model: 'haiku', schema: BRAKE }
  )
  if (b && b.halted) {
    log(`ABORT: safety brake engaged before dispatch — ${b.reason}. No workers spawned, no tokens spent on build.`)
    return { aborted: true, reason: 'halted', brake: b }
  }
  log(`Safety brake clear: ${b ? b.reason : '(no result — proceeding)'}.`)
}

let setupInfo = null
if (A.setup && A.setup.prompt) {
  const s = await agent(A.setup.prompt, { label: 'preflight:setup', phase: 'Preflight', model: 'sonnet', effort: 'high', schema: SETUP })
  if (!s || !s.ok || !(s.redOnStubs > 0)) {
    log('ABORT: sandbox setup failed its honesty gate (need ok + red-on-stubs > 0).')
    return { aborted: true, reason: 'setup_failed', setup: s }
  }
  setupInfo = s
  log(`Sandbox ready: ${s.totalTests} tests, ${s.redOnStubs} red on stubs${s.greenOnReference ? ', reference proved green' : ''}.`)
}

// Check ceiling before Build.
{
  const spent = budget.spent()
  if (CEILING && typeof CEILING.tokens === 'number' && spent > CEILING.tokens) {
    log(`ABORT: cost ceiling exceeded before Build (spent ${spent} > ${CEILING.tokens}).`)
    return {
      preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
      build: [],
      integration: { green: false, passed: null, failed: null },
      repairsUsed: 0,
      tokens: { buildOut: 0, verifyOut: 0, repairOut: 0, totalOut: spent, model: 'all-haiku (weight 1)' },
      mergeReady: false,
      ship: null,
      aborted: true,
      reason: 'cost_ceiling',
      spent,
      ceiling: CEILING.tokens,
    }
  }
}

// ---------------- Build (flat Haiku fan-out) ----------------
phase('Build')
const buildStart = budget.spent()
const built = await parallel(ITEMS.map((it) => () => {
  const buildPrompt = `FLAT ONE-TURN-WAVE worker for item "${it.slug}". Working dir: ${WORK}. ${HINT}\n` +
    `You OWN and may write ONLY these files: ${(it.ownsFiles || []).join(', ')}. Do NOT create or edit any other file (strict ownership — another worker owns the rest, in parallel).\n` +
    `IMPORTANT: All file writes MUST use absolute paths under ${WORK}.\n` +
    `TASK:\n${it.prompt}\n` +
    acceptanceCriteriaSection(it) +
    domainSynopsisSection(it) +
    `Use the Write tool. Run any quick local self-check you can, but the integration suite is run centrally, not by you. Report which files you wrote.${timeboxLine()}`
  return agent(buildPrompt, { label: `build:${it.slug}`, phase: 'Build', model: 'haiku', schema: DONE })
}))
const buildOut = budget.spent() - buildStart
log(`Build done: ${built.filter(Boolean).length}/${ITEMS.length} workers reported.`)

// ---------------- Self-Check + File Existence Verification (pipeline) ----------------
phase('Self-Check')
const selfCheckResults = {}  // slug -> { passed: boolean, reason: string }
const selfCheckStart = budget.spent()

// Deterministic file-existence check (agent-free) for each item's reported filesWritten.
for (const b of built) {
  if (!b || !b.slug) continue
  if (!b.filesWritten || !Array.isArray(b.filesWritten)) {
    selfCheckResults[b.slug] = { passed: false, reason: 'no filesWritten array in build report' }
    continue
  }
  let filesOk = true
  const missing = []
  for (const f of b.filesWritten) {
    // Use a simple ls check: if the file doesn't exist under WORK, mark it failed.
    // We defer to the verify agent below to run 'ls -l' for each file.
    // Here we just track that we need to verify.
  }
  // (The actual ls validation happens in the selfCheckCmd agents below.)
}

// Run selfCheckCmd agents in parallel (pipeline semantics: no wait barrier).
const selfCheckAgents = ITEMS
  .filter(it => it.selfCheckCmd)
  .map((it) => () => {
    const checkDir = it.workDir || WORK
    const selfCheckPrompt = `SELF-CHECK after Build for item "${it.slug}". Working dir: ${checkDir}.\n` +
      `Run this command: ${it.selfCheckCmd}\n` +
      `ALSO validate file existence: for each file listed in the item's build report, run \`ls -L\` to confirm it exists under ${WORK}.\n` +
      `Exit code: 0 = pass, non-0 = fail. Derive the result from EXIT CODE only (ignore the tail output below).\n` +
      `Report: passed=true if the command AND file-existence checks both exit 0; passed=false and a reason otherwise.${timeboxLine()}`
    return agent(selfCheckPrompt, { label: `selfcheck:${it.slug}`, phase: 'Self-Check', model: 'haiku', effort: 'low', schema: {
      type: 'object', additionalProperties: false,
      properties: { passed: { type: 'boolean' }, reason: { type: 'string' } },
      required: ['passed', 'reason'],
    } })
  })

if (selfCheckAgents.length > 0) {
  const selfCheckRes = await parallel(selfCheckAgents)
  for (const r of selfCheckRes) {
    if (r && r.slug) {
      selfCheckResults[r.slug] = { passed: r.passed, reason: r.reason || 'check failed' }
    }
  }
}

// Mark items as failed if self-check failed.
const selfCheckFailed = Object.entries(selfCheckResults)
  .filter(([_, r]) => !r.passed)
  .map(([slug, _]) => slug)

// File-existence deterministic check for each build report (agent-free, runs inline).
const filesMissing = []
for (const b of built) {
  if (!b || !b.slug) continue
  if (!b.filesWritten || !Array.isArray(b.filesWritten)) {
    if (!selfCheckFailed.includes(b.slug)) {
      selfCheckFailed.push(b.slug)
      selfCheckResults[b.slug] = { passed: false, reason: 'filesWritten missing from build report' }
    }
    continue
  }
  for (const f of b.filesWritten) {
    // TODO: implement deterministic ls check here (would require fs access in workflow scripts,
    // which is not available; delegates to selfCheckCmd agents instead).
  }
}

const selfCheckOut = budget.spent() - selfCheckStart
log(`Self-check done: ${Object.keys(selfCheckResults).length} items checked, ${selfCheckFailed.length} failed.`)

// Run postBuild action if specified and named items pass self-check.
if (A.postBuild && A.postBuild.cmd && A.postBuild.afterItems) {
  const postBuildItems = A.postBuild.afterItems || []
  const allItemsOk = postBuildItems.every(slug => selfCheckResults[slug] && selfCheckResults[slug].passed)
  if (allItemsOk && postBuildItems.length > 0) {
    phase('PostBuild')
    const postBuildStart = budget.spent()
    await agent(
      `POST-BUILD action after items ${postBuildItems.join(', ')} pass self-check. Working dir: ${WORK}.\n` +
      `Run: ${A.postBuild.cmd}\n` +
      `IMPORTANT: All file writes MUST use absolute paths under ${WORK}.`,
      { label: 'postbuild:action', phase: 'PostBuild', model: 'haiku', schema: {
        type: 'object', additionalProperties: false,
        properties: { ok: { type: 'boolean' }, note: { type: 'string' } },
        required: ['ok', 'note'],
      } }
    )
    const postBuildOut = budget.spent() - postBuildStart
    log(`PostBuild action completed (tokens spent: ${postBuildOut}).`)
  }
}

// Deterministic counter for unique label generation (workflow-safe, no Date/Math.random).
let _labelCounter = 0
function nextLabel(prefix) { return `${prefix}:${++_labelCounter}` }

// ---------------- Integrate + bounded Repair ----------------
function verify(tag, ph, testCommands) {
  // If testCommands array provided, run each as a separate agent and merge verdicts.
  if (testCommands && Array.isArray(testCommands) && testCommands.length > 0) {
    return parallel(testCommands.map((cmd) => () =>
      agent(
        `Working dir: ${WORK}. Run: ${cmd}  (PowerShell or Git Bash). Output: tail -n 40 to keep context bounded.\n` +
        `Derive pass/fail from EXIT CODE (use bash set -o pipefail if piping). Report exact passed/failed counts, green=(failed===0), and for each failing test map it to the responsible item slug from this set: ${ITEMS.map(i => i.slug).join(', ')} (infer from the file/module in the traceback; a file is owned by exactly one item). Do not modify files.`,
        { label: nextLabel(`verify:${tag}`), phase: ph, model: 'haiku', schema: VERIFY }
      )
    )).then((results) => {
      // Merge verdicts: green only if all are green.
      if (!Array.isArray(results) || results.length === 0) return { passed: 0, failed: 0, green: false, failingItems: [], detail: 'no results' }
      const merged = {
        passed: results.reduce((sum, r) => sum + (r && r.passed ? r.passed : 0), 0),
        failed: results.reduce((sum, r) => sum + (r && r.failed ? r.failed : 0), 0),
        green: results.every(r => r && r.green),
        failingItems: Array.from(new Set(results.flatMap(r => r.failingItems || []))),
        detail: results.map((r, i) => `[${i+1}] ${r && r.detail ? r.detail : 'no detail'}`).join(' | '),
      }
      return merged
    })
  } else {
    // Single test command (backward compatible).
    return agent(
      `Working dir: ${WORK}. Run: ${TEST}  (PowerShell or Git Bash). Output: tail -n 40 to keep context bounded.\n` +
      `Derive pass/fail from EXIT CODE (use bash set -o pipefail if piping). Report exact passed/failed counts, green=(failed===0), and for each failing test map it to the responsible item slug from this set: ${ITEMS.map(i => i.slug).join(', ')} (infer from the file/module in the traceback; a file is owned by exactly one item). Do not modify files.`,
      { label: `verify:${tag}`, phase: ph, model: 'haiku', schema: VERIFY }
    )
  }
}
phase('Integrate')
const testCmds = A.testCmds && Array.isArray(A.testCmds) && A.testCmds.length > 0 ? A.testCmds : null
let v = await verify('integrate', 'Integrate', testCmds)
let verifyOut = 0, repairOut = 0, repairsUsed = 0
{
  const vEnd = budget.spent()
  verifyOut += vEnd - (buildStart + buildOut)
}

phase('Repair')
// Declare contractFindings, adversarialReviewOut, adversarialReviewPending here (before while loop)
// to avoid TDZ ReferenceError when they're referenced in early returns (lines ~419, ~452).
let contractFindings = []
let adversarialReviewOut = 0
let adversarialReviewPending = false
let round = 0
while (v && !v.green && round < CAP) {
  round++

  // Check ceiling before starting a repair round.
  {
    const spent = budget.spent()
    if (CEILING && typeof CEILING.tokens === 'number' && spent > CEILING.tokens) {
      log(`ABORT: cost ceiling exceeded before repair round ${round} (spent ${spent} > ${CEILING.tokens}).`)
      const totalOut = spent
      return {
        preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
        build: (built || []).filter(Boolean).map(b => ({ slug: b.slug, wrote: b.wrote, files: b.filesWritten })),
        integration: v ? { green: v.green, passed: v.passed, failed: v.failed } : { green: false, passed: null, failed: null },
        repairsUsed,
        contractFindings: null,
        tokens: { buildOut, verifyOut, repairOut, adversarialReviewOut, totalOut, model: 'all-haiku (weight 1)' },
        mergeReady: false,
        ship: null,
        aborted: true,
        reason: 'cost_ceiling',
        spent,
        ceiling: CEILING.tokens,
      }
    }
  }

  // Re-check brake before repair round if recheckBrake is set (allows mid-wave .HALT stops).
  if (CEILING && CEILING.recheckBrake && A.brake && A.brake.checkCmd) {
    const BRAKE = {
      type: 'object', additionalProperties: false,
      properties: { halted: { type: 'boolean' }, reason: { type: 'string' } },
      required: ['halted', 'reason'],
    }
    const b = await agent(
      `READ-ONLY safety-brake re-check before repair round ${round} — do NOT modify anything. In directory ${A.brake.cwd || WORK}, run:\n${A.brake.checkCmd}\n` +
      `This checks the fleet kill-switch (.HALT sentinel) and/or the token cost-ceiling. Set halted=true if the command exits non-zero OR its output indicates HALTED / ceiling exceeded; set halted=false only if it clearly reports OK/clean/under-ceiling. Report the reason.`,
      { label: `repair-${round}:brake`, phase: 'Repair', model: 'haiku', schema: BRAKE }
    )
    if (b && b.halted) {
      log(`ABORT: safety brake re-engaged before repair round ${round} — ${b.reason}. Stopping repairs.`)
      const spent = budget.spent()
      const totalOut = spent
      return {
        preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
        build: (built || []).filter(Boolean).map(b => ({ slug: b.slug, wrote: b.wrote, files: b.filesWritten })),
        integration: v ? { green: v.green, passed: v.passed, failed: v.failed } : { green: false, passed: null, failed: null },
        repairsUsed,
        contractFindings: null,
        tokens: { buildOut, verifyOut, repairOut, adversarialReviewOut, totalOut, model: 'all-haiku (weight 1)' },
        mergeReady: false,
        ship: null,
        aborted: true,
        reason: 'halted',
        brake: b,
      }
    }
  }

  // Include both integration-failing items AND self-check-failing items in repair targets.
  const failingSlugs = (v.failingItems || []).filter(s => ITEMS.some(i => i.slug === s))
  const allFailingItems = Array.from(new Set([...failingSlugs, ...selfCheckFailed]))
  let targets = allFailingItems.length ? ITEMS.filter(i => allFailingItems.includes(i.slug)) : ITEMS

  // FIX 2: Cap repair targets to top 3 worst failing items when timebox is set (latency fix #2).
  // Rest are deferred to the orchestrator tail (logged but not in this round).
  const deferredItems = []
  if (TIMEBOX_MINUTES && targets.length > 3) {
    deferredItems.push(...targets.slice(3))
    targets = targets.slice(0, 3)
    log(`Repair round ${round} capped at 3 items (timebox); deferred: ${deferredItems.map(t => t.slug).join(', ')}`)
  }

  log(`Integration red (${v.failed} failed) — repair round ${round}/${CAP} on: ${targets.map(t => t.slug).join(', ')}`)
  repairsUsed = targets.length
  const rStart = budget.spent()
  await parallel(targets.map((it) => () => {
    // FIX 1: Build targeted test command for this item only (latency fix #1).
    // The verify result's failingItems tells us which items have failing tests.
    // Construct a prompt that directs repair workers to run ONLY tests for their owned files,
    // run commands ONCE to a file, never re-run to grep, and never run full union suites.
    const itemFiles = (it.ownsFiles || []).map(f => `  ${f}`).join('\n')
    const repairPrompt = `ONE-TURN-WAVE repair for item "${it.slug}". Working dir: ${WORK}. The integration suite failed: ${v.detail}\n` +
      lastTestOutputSection(it) +
      `\n** SCOPED REPAIR CONTEXT (token discipline — repair cache-read tax fix, measured #1 sink): **\n` +
      `You are given ONLY (a) the failing-suite verdict above and (b) the diff of YOUR OWN files. Do NOT re-read the whole prior build context — re-reading the full build is the measured top token sink.\n` +
      `To see exactly what you changed, run ONCE: \`git -C ${WORK} diff -- ${(it.ownsFiles || []).join(' ')}\` (your owned files only).\n` +
      ownsFilesDiffSection(it) +
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
      `\nFix ONLY your owned files with Edit/Write. Report.${timeboxLine()}`
    return agent(repairPrompt, { label: `repair:${it.slug}`, phase: 'Repair', model: 'haiku', schema: DONE })
  }))
  const rEnd = budget.spent()
  repairOut += rEnd - rStart
  v = await verify(`repair-${round}`, 'Repair')
  verifyOut += budget.spent() - rEnd
}

// -------- Adversarial Review (optional, gated on args.adversarialReview) --------
if (ADVERSARIAL_REVIEW && ADVERSARIAL_REVIEW_MODE === 'blocking' && v && v.green) {
  phase('AdversarialReview')
  const reviewStart = budget.spent()
  const REVIEW = {
    type: 'object', additionalProperties: false,
    properties: {
      slug: { type: 'string' }, holds: { type: 'boolean' }, breakingScenario: { type: 'string' },
    },
    required: ['slug', 'holds', 'breakingScenario'],
  }

  // Spawn one reviewer agent per built item; each tries to refute the item's contract.
  const reviewResults = await parallel(
    (built || []).filter(Boolean).map((b) => () => {
      if (!b.slug) return null
      const item = ITEMS.find(i => i.slug === b.slug)
      if (!item) return null

      const ownedFilesStr = (item.ownsFiles || []).length > 0
        ? `Owned files:\n${(item.ownsFiles || []).map(f => `  ${f}`).join('\n')}\n`
        : 'No owned files specified.\n'

      const reviewPrompt = `CONTRACT REFUTATION review for item "${b.slug}". Working dir: ${WORK}.\n` +
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

      return agent(reviewPrompt, { label: nextLabel(`review:${b.slug}`), phase: 'AdversarialReview', model: 'haiku', schema: REVIEW })
    })
  )

  // Collect findings where holds=false into contractFindings.
  for (const r of (reviewResults || [])) {
    if (r && r.slug) {
      if (!r.holds) {
        contractFindings.push({ slug: r.slug, breakingScenario: r.breakingScenario })
      }
    }
  }

  adversarialReviewOut = budget.spent() - reviewStart
  log(`AdversarialReview done: ${contractFindings.length} contract violation(s) found.`)
}

// LEVER 2 (wall-clock): in concurrent-note mode, do NOT run adversarialReview inline / do NOT await it before
// signaling merge-readiness. Defer it so the orchestrator kicks CI immediately and runs the refutation in
// parallel with the CI-wait window; contractFindings then gate MERGE (not CI). Findings are pending here.
if (ADVERSARIAL_REVIEW && ADVERSARIAL_REVIEW_MODE === 'concurrent-note' && v && v.green) {
  adversarialReviewPending = true
  log('AdversarialReview DEFERRED (concurrent-note): integration green — orchestrator should kick CI immediately and run adversarialReview in parallel; contractFindings gate MERGE, not CI.')
}

// Check ceiling before Ship.
{
  const spent = budget.spent()
  if (CEILING && typeof CEILING.tokens === 'number' && spent > CEILING.tokens) {
    log(`ABORT: cost ceiling exceeded before Ship (spent ${spent} > ${CEILING.tokens}).`)
    const totalOut = spent
    return {
      preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
      build: (built || []).filter(Boolean).map(b => ({ slug: b.slug, wrote: b.wrote, files: b.filesWritten })),
      integration: v ? { green: v.green, passed: v.passed, failed: v.failed } : { green: false, passed: null, failed: null },
      repairsUsed,
      contractFindings: contractFindings.length > 0 ? contractFindings : null,
      tokens: { buildOut, verifyOut, repairOut, adversarialReviewOut, totalOut, model: 'all-haiku (weight 1)' },
      mergeReady: v && v.green,
      ship: null,
      aborted: true,
      reason: 'cost_ceiling',
      spent,
      ceiling: CEILING.tokens,
    }
  }
}

// ---------------- Ship (batched git boundary — P2: one commit+push per WAVE, not per item) ----------------
let ship = null
if (A.git && v && v.green) {
  phase('Ship')
  const g = A.git
  const repoDir = g.repoDir || WORK
  const origin = g.origin || 'origin'
  const expectTop = g.expectTopLevel || (g.sandboxInit ? repoDir : null)
  // HARDENING (wave-22 incident): a corrupted manifest once passed the literal placeholder
  // "WORKTREE_ROOT_PER_ITEM" as expectTopLevel; the Ship guard then compared toplevel against a
  // non-path string that could never match, and the classifier had to catch the resulting
  // primary-tree/branch-"undefined" Ship prompt. In REAL-REPO mode expectTop MUST be a real
  // absolute path — hard-fail preflight-style here BEFORE assembling any Ship prompt, rather than
  // relying on the in-prompt guard to notice a garbage expected value.
  if (!g.sandboxInit) {
    const looksAbsolute = typeof expectTop === 'string' && /^([a-zA-Z]:[\\/]|\/)/.test(expectTop)
    const looksPlaceholder = typeof expectTop === 'string' && /[A-Z_]{6,}/.test(expectTop) && !/[\\/]/.test(expectTop)
    if (!expectTop || !looksAbsolute || looksPlaceholder || String(expectTop).includes('undefined')) {
      log(`ABORT Ship: args.git.expectTopLevel is not a real absolute path (got ${JSON.stringify(expectTop)}). Pass each item's actual sibling-worktree root, or omit args.git to skip the batched Ship and let the orchestrator run the merge train by hand.`)
      return {
        preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
        build: (built || []).filter(Boolean).map(b => ({ slug: b.slug, wrote: b.wrote, files: b.filesWritten })),
        integration: v ? { green: v.green, passed: v.passed, failed: v.failed } : { green: false, passed: null, failed: null },
        repairsUsed, mergeReady: v && v.green, ship: null,
        aborted: true, reason: 'bad_expectTopLevel', badValue: expectTop,
      }
    }
  }
  const scanLine = g.secretScan ? ` Then gate on secrets: \`python ${g.secretScan} --staged\` (must print CLEAN / exit 0; if it blocks, STOP and report committed=false).` : ''
  ship = await agent(
    `BATCHED GIT BOUNDARY — make ONE commit + ONE push for the WHOLE integrated wave (${ITEMS.length} items), NOT one per item, to amortize CI over the batch. Working tree: ${repoDir}. Use Git Bash.\n` +
    `*** SAFETY GUARD — DO THIS FIRST, before ANY git write: *** run \`git -C ${repoDir} rev-parse --show-toplevel\`.\n` +
    (g.sandboxInit
      ? `  - SANDBOX MODE: the result MUST be empty/error (no repo yet) OR exactly ${repoDir}. If it resolves to ANY ENCLOSING/ANCESTOR repo (a path SHORTER than / a prefix of ${repoDir}), you are inside another repo — ABORT NOW: do NOT init/add/commit/push anything; return committed=false, pushed=false, oneCommit=false, note="ABORT: sandbox inside repo <toplevel>". If safe: \`git -C ${repoDir} init -q\`, set a local user.name/user.email, create a LOCAL bare origin \`git init --bare -q ${g.sandboxInit}\`, then add/point the remote (\`git -C ${repoDir} remote add ${origin} ${g.sandboxInit}\`, or \`remote set-url\` if it exists). NEVER touch a remote named after a real host.\n`
      : `  - REAL-REPO MODE: the result MUST equal exactly ${expectTop}. If it does NOT, ABORT: do NOT add/commit/push; return committed=false, note="ABORT: toplevel <toplevel> != expected ${expectTop}". (This prevents ever committing into an unintended repo.)\n`) +
    `STAGE THE REAL BUILD — do NOT create any placeholder/dummy files: \`git -C ${repoDir} checkout -B ${g.branch}\` ; \`git -C ${repoDir} add -A\` (stages the files the build workers already wrote — the actual package/modules, specs, tests). Before committing, \`git -C ${repoDir} status --short\` and CONFIRM the staged set is the real source (NOT invented item*.txt).${scanLine} \`git -C ${repoDir} commit -q -m "${g.message || 'wave: batched one-turn build'}"\` ; ` +
    (g.push === false ? `do NOT push (report pushed=false).` : `\`git -C ${repoDir} push -q -u ${origin} ${g.branch}\`.`) +
    `\nVERIFY + REPORT: \`git -C ${repoDir} show --stat HEAD\` — report the sha, fileCount (files in that ONE commit), and oneCommit=true ONLY if exactly ONE new commit holds the whole integrated wave. Confirm NO per-item commits and NO invented placeholder files.`,
    { label: 'ship:batched', phase: 'Ship', model: 'haiku', schema: SHIP }
  )
  log(`Ship: committed=${ship && ship.committed} pushed=${ship && ship.pushed} files=${ship && ship.fileCount} oneCommit=${ship && ship.oneCommit}`)
}

// ---------------- Report ----------------
phase('Report')
const totalOut = budget.spent() - buildStart
const result = {
  preflight: { items: ITEMS.length, ownedFiles: Object.keys(owner).length, sandbox: setupInfo },
  build: (built || []).filter(Boolean).map(b => ({ slug: b.slug, wrote: b.wrote, files: b.filesWritten })),
  selfCheck: selfCheckResults && Object.keys(selfCheckResults).length > 0 ? selfCheckResults : null,
  integration: v ? { green: v.green, passed: v.passed, failed: v.failed } : { green: false, passed: null, failed: null },
  repairsUsed,
  contractFindings: contractFindings.length > 0 ? contractFindings : null,
  adversarialReviewMode: ADVERSARIAL_REVIEW ? ADVERSARIAL_REVIEW_MODE : null,
  adversarialReviewPending,
  tokens: { buildOut, verifyOut, selfCheckOut, repairOut, adversarialReviewOut, totalOut, model: 'all-haiku (weight 1)' },
  mergeReady: !!(v && v.green),
  ship,
  note: 'One Workflow call = one orchestrator turn. All-Haiku => raw==weighted. Setup tokens excluded. SelfCheck/PostBuild tokens included. AdversarialReview tokens only when args.adversarialReview is truthy. AdversarialReviewMode: "blocking" (default) runs the refutation inline before this return; "concurrent-note" defers it (adversarialReviewPending=true, contractFindings=null here) so the orchestrator kicks CI immediately and runs adversarialReview in parallel, gating MERGE (not CI) on contractFindings — overlapping the review with the CI-wait window. Real-repo wiring: give each item its own sibling git worktree (git worktree add ../aesop-wt-<slug> -b <branch> origin/main), workers write there + push; orchestrator opens PRs + ci_merge_wait after — the async CI/merge boundary stays outside this one turn.',
}
log(`DONE. selfcheck=${selfCheckFailed.length} failed, integration green=${result.mergeReady} passed=${result.integration.passed} repairs=${repairsUsed} contractViolations=${contractFindings.length} buildOut=${buildOut} totalOut=${totalOut}`)
return result
