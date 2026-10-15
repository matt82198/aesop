#!/usr/bin/env node
// PreToolUse hook — DENY reading session transcripts as if they were the system of record.
//
// RULE: session transcripts are NOT the system of record. The durable record is STATE.md,
// BUILDLOG.md, MEMORY.md, git, and the artifacts on disk. Conversation history is the thing
// a filesystem-as-brain architecture exists so an agent does not have to depend on it, and
// the standing rule that limits an orchestrator to reading its own control files follows
// directly from that.
//
// EVIDENCE: mining a transcript breaks three things at once.
//   1. It is unbounded context. A session transcript can run to tens of megabytes, and a
//      grep can dump an arbitrary amount of stale conversation into the most expensive
//      context in the fleet.
//   2. It is CIRCULAR EVIDENCE. A transcript holds the model's own prior output, so a hit
//      proves only that it once said the thing. This rule exists because a real search for
//      a production event name returned the searcher's own earlier words, and those were
//      nearly reported back as found data.
//   3. It hides the real defect. If a fact mattered and is not in a control file, the
//      failure is the MISSING CHECKPOINT. Recovering the fact from scrollback repairs the
//      symptom and leaves the brain still missing it, so the next session re-derives it
//      again from scratch.
//
// WHAT TO DO INSTEAD, in order: STATE.md (intent, decisions, NEXT STEPS) -> BUILDLOG.md
// (append-only snapshots) -> MEMORY.md and the memory tree -> git log / the artifacts the
// work produced -> re-run the measurement. If none of those hold it, write the checkpoint now.
//
// DENIED:  any read/search of ~/.claude/projects/**/*.jsonl (cat, grep, rg, head, tail, sed,
//          awk, python, node, Read/Grep/Glob tool paths). This location is the same for
//          every Claude Code user regardless of OS -- only the home-directory prefix differs
//          (e.g. `/home/<user>/.claude/projects/...` on Linux/macOS vs.
//          `C:\Users\<user>\.claude\projects\...` on Windows) -- so the match below is
//          anchored on `.claude/projects` with either slash and os.homedir() is used
//          wherever this hook itself needs the home directory (never a hardcoded path).
// ALLOWED: everything else, including ls/stat of that directory (knowing a session exists is
//          not the same as mining its contents), and every control file. The `memory/`
//          subtree under a project's `projects/<project>/memory/` stays readable -- it IS
//          part of the durable record.
//
// Escape hatch: [[ALLOW-TRANSCRIPT-READ]] anywhere in the command allows + logs (never silent).
// Wire via a PreToolUse hook, matcher "Bash|Read|Grep|Glob" (see hooks/CLAUDE.md).

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const ESCAPE_HATCH = '[[ALLOW-TRANSCRIPT-READ]]';
const STDIN_TIMEOUT_MS = 2000;

function readStdin(timeoutMs = STDIN_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (settled) return; settled = true; clearTimeout(t); resolve(v); };
    const t = setTimeout(() => finish(''), timeoutMs);
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { data += c; });
    process.stdin.on('end', () => finish(data));
    process.stdin.on('error', () => finish(''));
  });
}

function logEscapeUse(payload, detail) {
  try {
    const dir = path.join(os.homedir(), '.claude');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'TRANSCRIPT-READ-ESCAPES.log'), JSON.stringify({
      ts: new Date().toISOString(),
      tool: payload.tool_name,
      session_id: typeof payload.session_id === 'string' ? payload.session_id : null,
      detail: String(detail).slice(0, 200),
    }) + '\n');
  } catch { /* best effort, never block */ }
}

// A session transcript: <...>/.claude/projects/<project-key>/<uuid>.jsonl
// Matches forward and backslashes, quoted or bare, and a bare *.jsonl glob in that directory,
// regardless of what comes before `.claude` (POSIX home, Windows home, a relative path, etc).
const TRANSCRIPT = /\.claude[\/\\]projects[\/\\][^\s"']*?(\.jsonl|\*\.jsonl|[\/\\]\*)/i;
// Also catch `cd <projects dir> && grep ... *.jsonl`, where the two halves are separated.
const PROJECTS_DIR = /\.claude[\/\\]projects\b/i;
const READS_JSONL = /\b(cat|grep|rg|egrep|fgrep|head|tail|sed|awk|less|more|strings|python[0-9.]*|node|jq)\b[^\n]*\.jsonl/i;

export function verdictFor({ toolName, command, paths }) {
  const haystacks = [command, ...(paths || [])].filter(Boolean);
  for (const h of haystacks) {
    const hitsTranscript = TRANSCRIPT.test(h);
    const hitsSplit = PROJECTS_DIR.test(h) && /\.jsonl/i.test(h);
    const hitsReader = toolName === 'Bash' && READS_JSONL.test(h) && PROJECTS_DIR.test(h);
    // A file tool (Read/Grep/Glob) aimed INTO the projects tree is transcript mining even when
    // the path never spells ".jsonl" -- that directory holds transcripts. The one exception is
    // the memory subtree, which lives there and IS part of the durable record.
    const hitsTree = toolName !== 'Bash' && PROJECTS_DIR.test(h) && !/[\/\\]memory\b|MEMORY\.md/i.test(h);
    if (hitsTranscript || hitsSplit || hitsReader || hitsTree) {
      return (
        'Reading session transcripts is blocked — scrollback is not the system of record.\n\n' +
        'The durable record is STATE.md, BUILDLOG.md, MEMORY.md, git, and the artifacts the work ' +
        'produced. Conversation history is the thing a filesystem-as-brain architecture exists so ' +
        'you do not have to depend on it, and the standing rule limiting an orchestrator to its ' +
        'own control files follows directly from that.\n\n' +
        'Three reasons this is denied rather than merely discouraged:\n' +
        '  1. Unbounded context — these files are tens of MB and a grep can flood the main thread.\n' +
        '  2. Circular evidence — a transcript holds your OWN prior output, so a hit proves only ' +
        'that you once said it. This rule exists because a search for a production event name ' +
        'once returned the searcher\'s own words and was nearly reported as data.\n' +
        '  3. It hides the real defect — if the fact mattered and is not in a control file, the ' +
        'bug is the MISSING CHECKPOINT. Mining scrollback fixes the symptom and leaves the brain ' +
        'still missing it.\n\n' +
        'Do instead, in order: STATE.md -> BUILDLOG.md -> MEMORY.md -> git log / artifacts -> ' +
        're-run the measurement. If none of them hold it, write the checkpoint now.\n\n' +
        'Deliberate exception: add [[ALLOW-TRANSCRIPT-READ]] to the command (logged, never silent).'
      );
    }
  }
  return null;
}

function emit(decision, reason) {
  const out = { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decision } };
  if (reason) out.hookSpecificOutput.permissionDecisionReason = reason;
  console.log(JSON.stringify(out));
  process.exit(0);
}

function main(raw) {
  let j = {};
  try { j = JSON.parse(raw || '{}'); } catch { return emit('allow'); }
  const toolName = j.tool_name;
  const input = j.tool_input;
  if (!input || typeof input !== 'object') return emit('allow');

  const command = typeof input.command === 'string' ? input.command : '';
  const paths = [input.file_path, input.path, input.pattern, input.glob]
    .filter((v) => typeof v === 'string');

  if (command.includes(ESCAPE_HATCH)) {
    logEscapeUse(j, command);
    return emit('allow', 'transcript read permitted via [[ALLOW-TRANSCRIPT-READ]] (logged)');
  }

  const reason = verdictFor({ toolName, command, paths });
  return reason ? emit('deny', reason) : emit('allow');
}

if (process.argv[1] && process.argv[1].endsWith('no-scrollback-as-record.mjs')) {
  readStdin().then(main);
}
