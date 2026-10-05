export const meta = {
  name: 'ci-to-green',
  description: 'Carry a jammed PR queue to green, merge it, then fix and PROVE the gate that should have caught the failure',
  whenToUse: 'Whenever the orchestrator is hung up on CI or a merge train. Clusters failures by ROOT CAUSE rather than by PR, fixes each cause once across every affected branch, merges with MERGED-state proof, then builds the missing pre-CI gate and adversarially proves it would have caught the original failure.',
  phases: [
    { title: 'Recon', detail: 'snapshot every open PR: mergeable state, failing checks, real logs' },
    { title: 'Cluster', detail: 'group failures by ROOT CAUSE, not by PR', model: 'sonnet' },
    { title: 'Fix', detail: 'one agent per cause, applied across all affected branches', model: 'sonnet' },
    { title: 'Verify', detail: 'confirm each PR actually went green' },
    { title: 'Merge', detail: 'serial train, MERGED-state verified per PR' },
    { title: 'Enforce', detail: 'build the pre-CI gate that should have caught each cause', model: 'sonnet' },
    { title: 'Prove', detail: 'independent adversary: replay the original failure, check false positives', model: 'sonnet' },
  ],
}

const REPO = (args && args.repo) || 'C:/Users/matt8/aesop'
const MAX_CLUSTERS = (args && args.maxClusters) || 3
// Optional: restrict the whole run to specific PRs (e.g. a single integration batch).
// Without this, recon sweeps every open PR — wasteful when a batch supersedes them.
const FOCUS = (args && args.focusPrs) || null
const FOCUS_NOTE = FOCUS
  ? `\n\nSCOPE RESTRICTION: consider ONLY these PRs: ${FOCUS.join(', ')}. Ignore every other open PR — they are superseded by these and must not be touched, fixed, or reported on.`
  : ''

// Rules every agent in this workflow must carry. Learned the hard way; do not trim.
const RULES = `
HARD RULES (violating any of these fails your lane):
- Work in a WORKTREE. Never 'git checkout' in the primary tree — a hook blocks it, correctly.
- FORBIDDEN: git stash (the stash stack is shared across worktrees and cross-contaminates lanes),
  --no-verify, --admin, --auto, force-push, 'git commit --amend' on an already-pushed commit,
  manual 'gh pr merge', credential hunting (a missing key means SKIP + report, never a search).
- NEVER weaken, disable, or edit a gate to make CI pass. If a gate blocks wrongly, that is a
  finding to report, not an obstacle to route around.
- This repo's Python test runner is unittest ('npm run test:py' = 'python -m unittest discover -s
  tests'). NEVER pytest, and never write the word pytest in any doc or comment — a CI gate rejects it.
- Exit-code contract: 0 = clean, 1 = findings, 2 = COULD NOT EVALUATE. Never collapse 2 into 0.
  A gate that passes because it checked nothing is this repo's most recurrent defect.
- Run the ACTUAL gate command, never a proxy. 'vite build' is not 'tsc'; local is not headless CI.
- Verify claims before reporting them. Agents here have repeatedly reported "all gates passed" while
  shipping a broken import. Paste real command output, not summaries of it.
`

const RECON_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['prs', 'mainSha', 'mainCi'],
  properties: {
    mainSha: { type: 'string' },
    mainCi: { type: 'string', description: 'green | running | red' },
    prs: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['number', 'branch', 'mergeable', 'state', 'failingChecks', 'errorExcerpt'],
        properties: {
          number: { type: 'integer' },
          branch: { type: 'string' },
          mergeable: { type: 'string' },
          state: { type: 'string', description: 'GREEN | RED | PENDING | CONFLICTING' },
          failingChecks: { type: 'array', items: { type: 'string' } },
          errorExcerpt: { type: 'string', description: 'verbatim first real error line from the failing job log, empty if green' },
        },
      },
    },
  },
}

const CLUSTER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['clusters', 'greenPrs'],
  properties: {
    greenPrs: { type: 'array', items: { type: 'integer' }, description: 'PRs already green, no fix needed' },
    clusters: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'rootCause', 'evidence', 'affectedPrs', 'affectedBranches', 'fixSketch', 'whyCiCaughtItNotEarlier'],
        properties: {
          id: { type: 'string' },
          rootCause: { type: 'string', description: 'ONE sentence naming the actual cause, not the symptom' },
          evidence: { type: 'string', description: 'file:line or verbatim error proving it' },
          affectedPrs: { type: 'array', items: { type: 'integer' } },
          affectedBranches: { type: 'array', items: { type: 'string' } },
          fixSketch: { type: 'string' },
          whyCiCaughtItNotEarlier: { type: 'string', description: 'which local/pre-push gate SHOULD have caught this before CI did, or why none exists' },
        },
      },
    },
  },
}

const FIX_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['clusterId', 'branchesFixed', 'branchesFailed', 'gateOutput', 'notes'],
  properties: {
    clusterId: { type: 'string' },
    branchesFixed: { type: 'array', items: { type: 'string' } },
    branchesFailed: { type: 'array', items: { type: 'string' }, description: 'branch: verbatim reason' },
    gateOutput: { type: 'string', description: 'real output of the exact gate command, post-fix' },
    notes: { type: 'string' },
  },
}

const ENFORCE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['clusterId', 'gateBuilt', 'gatePath', 'branch', 'falsePositivesOnMain', 'notes'],
  properties: {
    clusterId: { type: 'string' },
    gateBuilt: { type: 'boolean' },
    gatePath: { type: 'string' },
    branch: { type: 'string' },
    falsePositivesOnMain: { type: 'array', items: { type: 'string' }, description: 'files the new gate flags on clean main — must be empty or explained' },
    notes: { type: 'string' },
  },
}

const PROVE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['clusterId', 'catchesOriginal', 'catchProof', 'falsePositiveClean', 'falsePositiveProof', 'verdict'],
  properties: {
    clusterId: { type: 'string' },
    catchesOriginal: { type: 'boolean', description: 'does the new gate FAIL when replayed against the original defect?' },
    catchProof: { type: 'string', description: 'verbatim command + output showing it fails on the reintroduced defect' },
    falsePositiveClean: { type: 'boolean', description: 'true if it wrongly fires on clean main' },
    falsePositiveProof: { type: 'string' },
    verdict: { type: 'string', description: 'PROVEN | NOT PROVEN — and why' },
  },
}

// ---------------------------------------------------------------- RECON

phase('Recon')
log('Snapshotting the PR board — reading real job logs, not check names')

const recon = await agent(
  `Read-only recon of the PR queue in ${REPO}. Make NO commits, NO edits, NO pushes.

For EVERY open PR (\`gh pr list --state open\`), collect:
- number, head branch, mergeable state, and whether it is GREEN / RED / PENDING / CONFLICTING
- the NAMES of failing checks
- **the verbatim first real error line from the failing job's log** — this is the important part.
  Check names like "ci (0)" are useless for clustering; the actual error is what groups failures.
  Get it via: gh pr view <N> --json statusCheckRollup --jq '...detailsUrl', extract the run id, then
  \`gh run view <id> --json jobs\` to find the failed STEP name, and \`gh run view <id> --log --job=<jid>\`
  to read the error. Grep for AssertionError / Error: / FAILED / Traceback / ModuleNotFoundError.

Also report main's SHA and whether main CI is green, running or red.

Be efficient: many PRs share one failure. Once you recognise an error you have already seen, record it
and move on rather than re-reading the whole log.${FOCUS_NOTE}

${RULES}`,
  { schema: RECON_SCHEMA, model: 'haiku', label: 'recon:board', phase: 'Recon' }
)

if (!recon || !recon.prs || recon.prs.length === 0) {
  log('Recon returned no PRs — nothing to do')
  return { status: 'no-prs', recon }
}

const redCount = recon.prs.filter((p) => p.state === 'RED' || p.state === 'CONFLICTING').length
log(`Board: ${recon.prs.length} open PRs, ${redCount} red/conflicting, main=${recon.mainSha} (CI ${recon.mainCi})`)

// ---------------------------------------------------------------- CLUSTER

phase('Cluster')
log('Grouping failures by ROOT CAUSE — the whole point: fix each cause once, not each PR once')

const clustered = await agent(
  `You are given a snapshot of a jammed PR queue. Group the failures by ROOT CAUSE.

THIS IS THE CENTRAL JUDGEMENT OF THE WHOLE RUN. Fixing per-PR multiplies work; fixing per-cause
divides it. In this repo a single cause has previously accounted for 14 red PRs at once.

SNAPSHOT:
${JSON.stringify(recon, null, 2)}

For each cluster give:
- a ONE-SENTENCE root cause naming the actual cause, NOT the symptom. "ci (0) fails" is a symptom.
  "tests/CLAUDE.md stores a hand-maintained count that every parallel branch invalidates" is a cause.
- evidence: file:line or the verbatim error
- every affected PR number and branch
- a fix sketch that can be applied ACROSS all affected branches
- **whyCiCaughtItNotEarlier**: which local or pre-push gate SHOULD have caught this before it ever
  reached CI — or state plainly that no such gate exists. This field drives the Enforce phase, so be
  concrete: name the gate, or name the gate that ought to exist.

Rules for clustering:
- Two PRs failing the same CHECK NAME with DIFFERENT errors are different clusters.
- Two PRs failing different check names with the SAME underlying error are ONE cluster.
- A PR that is merely CONFLICTING (not red) is its own cluster: cause is "branch out of date".
- Cap at ${MAX_CLUSTERS} clusters. If there are more distinct causes, keep the ${MAX_CLUSTERS} that
  unblock the most PRs and say in notes what you deferred — do NOT silently drop them.
- List already-green PRs separately in greenPrs; they need no fix and go straight to merge.

${RULES}`,
  { schema: CLUSTER_SCHEMA, model: 'sonnet', label: 'cluster:root-causes', phase: 'Cluster' }
)

if (!clustered) {
  log('Clustering failed — aborting rather than guessing')
  return { status: 'cluster-failed', recon }
}

const clusters = (clustered.clusters || []).slice(0, MAX_CLUSTERS)
log(`${clusters.length} root cause(s); ${(clustered.greenPrs || []).length} PR(s) already green`)
clusters.forEach((c) => log(`  [${c.id}] ${c.rootCause} → ${c.affectedPrs.length} PRs`))

// ---------------------------------------------------------------- FIX

phase('Fix')

const fixes = await parallel(
  clusters.map((c) => () =>
    agent(
      `Fix ONE root cause across every branch it affects. Sonnet-class judgement is expected here.

ROOT CAUSE: ${c.rootCause}
EVIDENCE: ${c.evidence}
FIX SKETCH: ${c.fixSketch}
AFFECTED BRANCHES: ${(c.affectedBranches || []).join(', ')}
AFFECTED PRs: ${(c.affectedPrs || []).join(', ')}

Repo: ${REPO}.

METHOD:
1. Reproduce the failure locally FIRST on one affected branch. Do not fix what you have not seen fail.
   If you cannot reproduce it, say so and stop — a fix for an unreproduced failure is a guess.
2. Apply the fix to EVERY affected branch, in its own worktree.
3. Before pushing each branch, run the EXACT gate that was failing, plus the standard set:
     python -m compileall -q tools/
     python -m unittest tests.test_tools_importable
     python tools/encoding_lint.py
     python tools/claudemd_sync_gate.py
     python C:/Users/matt8/scripts/secret_scan.py --staged
4. Push to the EXISTING branch (this updates its PR). Do not open new PRs, do not merge.
5. If one branch cannot be fixed, record it in branchesFailed with the VERBATIM error and MOVE ON —
   one branch must never stall the others.

CONFLICT RULE: conflicts in tests/CLAUDE.md and tools/CLAUDE.md are independent appends from
different lanes. Keep BOTH sides. Never delete another lane's entry to clear a conflict.

Report the real gate output post-fix, not a summary of it.

${RULES}`,
      { schema: FIX_SCHEMA, model: 'sonnet', label: `fix:${c.id}`, phase: 'Fix' }
    )
  )
)

const okFixes = fixes.filter(Boolean)
log(`Fix phase: ${okFixes.length}/${clusters.length} clusters processed`)

// ---------------------------------------------------------------- VERIFY

phase('Verify')

const verified = await agent(
  `Read-only. Re-check the PR board in ${REPO} after fixes were pushed.

Fix phase results:
${JSON.stringify(okFixes, null, 2)}

For every open PR report: number, mergeable state, GREEN/RED/PENDING, and for anything still red the
verbatim error. CI may still be running — report PENDING honestly rather than guessing an outcome.

Do NOT report a PR as green unless its checks actually show zero failures and zero pending.

${RULES}`,
  { schema: RECON_SCHEMA, model: 'haiku', label: 'verify:board', phase: 'Verify' }
)

const readyToMerge = (verified && verified.prs ? verified.prs : [])
  .filter((p) => p.state === 'GREEN' && p.mergeable === 'MERGEABLE')
  .map((p) => p.number)

log(`Verify: ${readyToMerge.length} PR(s) green and mergeable`)

// ---------------------------------------------------------------- MERGE

phase('Merge')

let mergeResult = null
if (readyToMerge.length === 0) {
  log('Nothing green to merge — skipping merge phase')
} else {
  mergeResult = await agent(
    `Merge these green, mergeable PRs in ${REPO}: ${readyToMerge.join(', ')}

USE THE REPO'S DETERMINISTIC SCRIPT, never 'gh pr merge' by hand:
    cd ${REPO} && python tools/merge_train.py -u ${readyToMerge.join(' ')} [[ALLOW-MERGE-TRAIN]]

A PreToolUse hook denies that script by default because the ORCHESTRATOR must never run it. You are
an agent, so appending the documented token [[ALLOW-MERGE-TRAIN]] is its SANCTIONED use and is logged.
Do not wrap the command in python -c/subprocess to evade any hook.

THE TREADMILL — expect it: if the queue shares a hand-maintained counter (e.g. a test-suite count in
tests/CLAUDE.md), each merge invalidates the remaining branches. Between merges, bring the next PR up
to date with origin/main and re-run any --fix the repo provides, then continue.

VERIFY EVERY MERGE — exit 0 is NOT proof of a merge:
    gh pr view <N> --json number,state,mergedAt
Require state == "MERGED" with a non-null mergedAt. Report the raw JSON for each. Any PR not MERGED
must be reported as NOT MERGED with its specific reason. Never report success on assumption.

Budget: main CI is slow. Merge as many as you can, in order, then stop cleanly with a report rather
than running indefinitely. Six verified merges beat a claim of sixteen.

Finally report: git rev-parse --short origin/main, and whether main CI is green/running/red.

${RULES}`,
    { model: 'haiku', label: 'merge:train', phase: 'Merge' }
  )
}

// ---------------------------------------------------------------- ENFORCE

phase('Enforce')
log('Building the gate that should have caught each cause BEFORE CI did')

const enforcements = await parallel(
  clusters.map((c) => () =>
    agent(
      `Build the missing wall for ONE root cause. This is the step that stops the failure recurring.

ROOT CAUSE: ${c.rootCause}
EVIDENCE: ${c.evidence}
WHY CI CAUGHT IT INSTEAD OF SOMETHING EARLIER: ${c.whyCiCaughtItNotEarlier}

Repo: ${REPO}.

The failure reached CI. CI is the LAST line of defence and the slowest — a 15-PR jam is what it costs
to find a defect there instead of locally. Your job: make this class of failure impossible to reach
CI again.

DECIDE which is right, and justify it:
(a) An EXISTING gate should have caught it but did not — because it fails open, scans the wrong
    scope, or has a hole. Fix that gate.
(b) NO gate covers this class. Build one: a new checker in tools/ following neighbouring conventions
    (--check default, --json, and the 0=clean / 1=findings / 2=could-not-evaluate contract, never
    exiting 0 having scanned nothing).

MANDATORY FALSE-POSITIVE SWEEP: run your gate across current origin/main and report EVERY file it
flags. Main is green, so anything it flags there is either a real latent instance of the same defect
(valuable — report it) or a false positive (fix your detector before shipping). A gate that lights up
a green main will be switched off, which is strictly worse than no gate.

Do NOT arm the gate in any hook or CI workflow. Build it, test it, push it on its own branch. Wiring
it in is a separate decision.

Add tests covering: the defect is caught, clean input passes, and zero-input exits 2.

Ship on a NEW branch named guard/<something-descriptive> off origin/main. Do not open a PR.

${RULES}`,
      { schema: ENFORCE_SCHEMA, model: 'sonnet', label: `enforce:${c.id}`, phase: 'Enforce' }
    )
  )
)

const builtGates = enforcements.filter(Boolean).filter((e) => e.gateBuilt)
log(`Enforce: ${builtGates.length} gate(s) built`)

// ---------------------------------------------------------------- PROVE

phase('Prove')
log('Independent adversary: would this gate actually have caught the original failure?')

const proofs = await parallel(
  builtGates.map((g) => {
    const c = clusters.find((x) => x.id === g.clusterId) || {}
    return () =>
      agent(
        `You are an INDEPENDENT ADVERSARY. Another agent built a gate and claims it prevents a defect
class. Your job is to REFUTE that claim. Default to NOT PROVEN when uncertain.

You did not build this gate and you have no stake in it passing. A gate that is waved through is worse
than no gate, because it creates false confidence — this repo has already shipped gates that reported
success while verifying nothing.

CLAIMED GATE: ${g.gatePath}   (branch: ${g.branch})
ORIGINAL ROOT CAUSE: ${c.rootCause || g.clusterId}
ORIGINAL EVIDENCE: ${c.evidence || 'see cluster'}

Repo: ${REPO}. Work in a worktree, read-only with respect to the gate — do not "fix" it.

TWO TESTS, both required:

1. **REPLAY THE ORIGINAL.** Reintroduce the original defect in a scratch worktree — literally
   recreate the condition described in the evidence — then run the new gate against it.
   The gate MUST fail (non-zero). Paste the verbatim command and output.
   If it does NOT fail, the gate does not do what it claims. That is the single most important
   finding you can return, and it means the whole run did not actually prevent recurrence.

2. **FALSE POSITIVES ON CLEAN STATE.** Run the gate against clean origin/main. It must NOT fire.
   Paste the verbatim output. If it fires, report every file it flags and judge whether each is a
   genuine latent instance or a detector bug.

Also probe the edges: what happens on empty input, on a file it cannot read, on a directory with no
matching files? If any of those exit 0, that is a fail-open path and the gate is decorative — report
it.

Return verdict PROVEN only if BOTH: it fails on the replayed original, AND it does not fire on clean
main. Anything else is NOT PROVEN, with the reason.

${RULES}`,
        { schema: PROVE_SCHEMA, model: 'sonnet', label: `prove:${g.clusterId}`, phase: 'Prove' }
      )
  })
)

const okProofs = proofs.filter(Boolean)
const proven = okProofs.filter((p) => p.verdict && p.verdict.indexOf('PROVEN') === 0 && p.catchesOriginal && !p.falsePositiveClean)
const unproven = okProofs.filter((p) => !(p.verdict && p.verdict.indexOf('PROVEN') === 0 && p.catchesOriginal && !p.falsePositiveClean))

log(`Prove: ${proven.length} gate(s) PROVEN, ${unproven.length} NOT PROVEN`)
unproven.forEach((p) => log(`  NOT PROVEN [${p.clusterId}]: ${p.verdict}`))

return {
  status: 'complete',
  board: { before: { prs: recon.prs.length, red: redCount, mainSha: recon.mainSha }, mainAfter: recon.mainSha },
  rootCauses: clusters.map((c) => ({ id: c.id, cause: c.rootCause, prs: c.affectedPrs, missingGate: c.whyCiCaughtItNotEarlier })),
  fixes: okFixes,
  merged: mergeResult,
  gates: builtGates,
  proofs: okProofs,
  proven: proven.map((p) => p.clusterId),
  notProven: unproven.map((p) => ({ id: p.clusterId, verdict: p.verdict })),
}
