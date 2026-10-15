# Governance & Control

**TL;DR**: Keep the system coherent: one instance of each loop (heartbeat protocol), single writer per control file (MEMORY.md, STATE.md), append-only logs, secret-scan gate on every push, feature branches only (never direct-to-main). Prevents races, data loss, credential leaks, and makes work auditable.

---

## Single-instance loops

Standing loops (watchdog, monitor, memory keeper, cost tracker, QA verification) must not run in duplicate — that causes contention, conflicting writes, and wasted work.

**Pattern**: Every loop checks its own heartbeat file before starting and skips the start if a live one exists.

### Heartbeat protocol

1. Before loop starts, check `~/.claude/loops/<loop-name>.heartbeat`
2. If exists and recent (<5 min old), skip and exit
3. If missing or stale, create/touch heartbeat file, run loop, delete heartbeat on exit

**Example** (pseudo-code):
```bash
HEARTBEAT=~/.claude/loops/memory-keeper.heartbeat
if [ -f "$HEARTBEAT" ] && [ $(($(date +%s) - $(stat -c %Y "$HEARTBEAT"))) -lt 300 ]; then
  exit 0  # Live keeper running, skip
fi
touch "$HEARTBEAT"
# ... run loop ...
rm "$HEARTBEAT"
```

**Why**: Prevents duplicate work, keeps system coherent, and ensures idempotent restarts.

## Single-writer control files

Some files must have exactly one writer to prevent race conditions and data loss.

**Writers by role**:
- **MEMORY.md**: memory keeper only
- **STATE.md**: active orchestrator only
- **BUILDLOG.md**: append-only (anyone can append, no one edits earlier entries)
- **Other loops**: append requests to an inbox file, never edit control files directly

**Why**: Contention on shared state causes corruption. Single-writer enforcement makes all edits safe.

## Inbox pattern for coordination

Loops that need to request memory updates or signal decisions use an append-only inbox. The orchestrator reviews inbox entries on every `/power` cycle. See [skills/power/SKILL.md](../skills/power/SKILL.md) for orchestrator priming details.

### Inbox format

1. Create `~/.claude/INBOX.md` (append-only, anyone can append)
2. Format: `[TIMESTAMP] <loop-name>: <request>`
3. On every `/power`, orchestrator reviews entries, acts on them, moves handled ones to an ACCEPTED/REJECTED log
4. Clear INBOX.md when processed

**Example**:
```
[2026-07-11 13:45] cost-loop: log monthly spend update to MEMORY.md
[2026-07-11 14:00] qa-loop: mark feature X as verified, move to shipped log
```

**Why**: Decouples loop work from orchestrator; loops queue requests without blocking; orchestrator batches decisions on a predictable cadence.

## Log rotation threshold

Append-only control files grow over time. When a file exceeds **~200 lines or ~20 KB**, rotate the oldest entries into a dated archive.

### Rotation procedure

1. Copy oldest entries (typically oldest 50%) to `<FILE>-YYYY-MM.md`
2. Keep only recent entries in the live `<FILE>.md`
3. Commit both old and new files
4. Update any references to point to the live file

**Examples**:
- `BUILDLOG.md` → `BUILDLOG-2026-06.md` (archive) + new `BUILDLOG.md` (live)
- `COST-LOG.md` → `COST-LOG-2026-06.md` (archive) + new `COST-LOG.md` (live)

**Why**: Keeps orchestrator context bounded and responsive. Orchestrator reads only recent ~20 entries; old data lives in archives.

## Branch workflow: never straight to main

**Production-grade branching** ensures code review, testing, and safety gates on all production merges.

### Feature & fix branches

- **Features**: `feat/<topic>` branches off main
- **Fixes**: `fix/<issue-number>` branches off main
- **All work**: Branches first, PR after implementation, merge after final-catch review

**Never** push directly to main/master. All changes go through:
1. Feature/fix branch
2. Open PR
3. Orchestrator final-catch review
4. Merge into main

### Session branches for infrastructure repos

- Daily work on infrastructure repos (e.g., `~/.claude`): use `work/session-<date>` branches (e.g., `work/session-2026-07-11`)
- Auto-commit hooks commit to the current branch, so housekeeping changes land on the session branch automatically
- On checkpoint, open a PR from session branch → main
- Orchestrator performs final-catch review; the merge is delegated to a dispatched agent (`python tools/auto_merge.py <n>`) and verified by `state == MERGED`

### Backup branches

Emergency backup snapshots use `backup/<snapshot-label>` branches. Never merge backups to main; recovery is via cherry-pick from backup → feature → PR → merge.

**Why**: Main branch stays deployable. All work visible in PRs. History auditable. Accidental commits prevented.

## Secret-scan gate on every push

**Critical rule**: Every push (to any repo, any branch) is preceded by:

```bash
python ~/scripts/secret_scan.py --staged --repo <repo_path>
```

### Gate logic

- **Exit code 0**: safe, proceed with push
- **Exit code 1**: secrets detected, block push until resolved

### What it detects

- AWS/API keys, OAuth tokens, credentials
- Private keys and SSH keys
- Hardcoded passwords or session tokens
- Sensitive file patterns

### On blocked push

1. Fix the staged files (remove secrets)
2. Re-run secret_scan to verify clean
3. Re-push (no `--no-verify` or workarounds)

**Why**: Credentials leak → account/system compromise. Scanning + gating prevents accidents and keeps repos clean.

## Version control for the brain

Infrastructure repos (`~/.claude` for rules/hooks, `~/scripts` for shared scripts, handoff folders) are version-controlled and pushed to private remotes.

**Discipline**:
1. Every rule/hook/skill/memory change is committed and pushed as it lands
2. On every `/power` session:
   - Verify repos are clean (no uncommitted changes)
   - Verify pushed to remote (no commits ahead)
   - If unexpected changes found, restore from remote before continuing
3. Never commit credentials, tokens, session data, or large binaries

**Why**: Infrastructure is durable, recoverable, and auditable. Unexpected changes to rules are a Tier-1 incident.

## Violations and escalation

Governance violations are detected and escalated:

1. **Watchdog daemon** enforces secret-scan gate and branch discipline
2. **Monitor agent** auto-detects control-file violations and stages PROPOSALS.md or escalates
3. **Orchestrator** reads STATE.md to verify governance compliance
4. **Violations caught late** are logged and triaged; never silently ignored

If a rule creates friction, propose a change via PROPOSALS.md (never work around it).

---

**Why these patterns matter**: They ensure your orchestration fleet operates **reliably** (inputs → outputs), **safely** (no secret leaks), **coherently** (no data loss from races), and **durably** (recoverable from failures). Together, they enable scaling from 1 orchestrator to dozens of parallel subagents without losing control or visibility.
