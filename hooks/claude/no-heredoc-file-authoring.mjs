#!/usr/bin/env node
// PreToolUse hook — DENY authoring file content through a shell heredoc.
//
// RULE: heredocs are banned outright in Bash commands — every form, including the
// interpreter forms `python - <<PY` / `node <<JS` / `sh <<SH`.
//
// EVIDENCE: authoring multi-line file content (HTML, Python, Markdown, JSON) through
// `cat > file <<EOF` fails silently and expensively. Real content contains apostrophes,
// double quotes, backticks and `$`. A quoted heredoc protects against shell expansion but
// NOT against the surrounding command line's own parsing, and any trailing `python -c '...'`
// or `grep "..."` in the same invocation can swallow a quote and kill the whole block. The
// file is then NOT written, and the entire payload has to be re-emitted — one real incident
// cost a full second transmission of an ~8KB file.
//
// The interpreter-piped form (`python - <<'PY'`) was tried as a narrower exception and kept
// failing the same way: the longest attempt died with "unexpected EOF while looking for
// matching '" and wrote nothing. There is no case where a heredoc beats writing a script
// file with the Write tool and then running it — the script survives quoting, a trailing
// command in the same invocation cannot eat it, and it stays inspectable, diffable and
// re-runnable afterwards instead of vanishing into scrollback.
//
// This is deliberately a GATE and not a memory. A memory is advisory and read after the
// fact; this refuses before the cost is paid. Write/Edit are the correct tools and cannot
// fail this way.
//
// DENIED:   EVERY heredoc in a Bash command. cat > f <<EOF, tee f <<EOF, python - <<PY,
//           node <<JS, sh <<SH, psql <<SQL — all of it.
// ALLOWED:  single-line `printf ... >> file`, `echo ... > file` (no heredoc)
// ALLOWED:  everything else, including a `<<` bit-shift inside a quoted code string (a
//           heredoc tag must start with a letter or underscore; a bit-shift operand doesn't).
//
// Escape hatch: [[ALLOW-HEREDOC-WRITE]] anywhere in the command allows + logs (never silent).
// Wire via a PreToolUse hook, matcher "Bash" (see hooks/CLAUDE.md for the registration).

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const ESCAPE_HATCH = '[[ALLOW-HEREDOC-WRITE]]';
const STDIN_TIMEOUT_MS = 2000;

function readStdin(timeoutMs = STDIN_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => finish(''), timeoutMs);
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => finish(data));
    process.stdin.on('error', () => finish(''));
  });
}

function logEscapeUse(payload, command) {
  try {
    const root = process.env.AESOP_ROOT || path.join(os.homedir(), 'aesop');
    const dir = path.join(root, 'state');
    fs.mkdirSync(dir, { recursive: true });
    const rec = {
      ts: new Date().toISOString(),
      event: 'heredoc_write_escape',
      tool: payload.tool_name,
      session_id: typeof payload.session_id === 'string' ? payload.session_id : null,
      cwd: typeof payload.cwd === 'string' ? payload.cwd : null,
      command_head: command.slice(0, 200)
    };
    fs.appendFileSync(path.join(dir, 'HEREDOC-WRITE-ESCAPES.log'), JSON.stringify(rec) + '\n');
  } catch {
    // audit logging is best-effort; never block on it
  }
}

// ANY heredoc. `<<` or `<<-`, an optionally quoted tag, then end of line -- which is the shape
// a heredoc always has and a `<<` bit-shift inside a quoted code string never does.
const ANY_HEREDOC = /<<-?\s*(['"]?)[A-Za-z_]\w*\1[^\n]*\r?\n/;

export function verdictFor(command) {
  if (!command) return null;
  if (ANY_HEREDOC.test(command)) {
    return (
      'Heredocs are banned in Bash commands. Write a script file with the Write tool, then run it.\n\n' +
      'Every form: `cat > f <<EOF`, `tee f <<EOF`, and `python - <<PY` / `node <<JS` / `sh <<SH` ' +
      'alike. The interpreter form used to be allowed here and it kept failing — the longest one ' +
      'died with "unexpected EOF while looking for matching \'" and wrote nothing, after which the ' +
      'entire payload had to be re-sent.\n\n' +
      'Why a script file always wins: it survives apostrophes, quotes, backticks and $ without ' +
      'escaping; a trailing command in the same invocation cannot swallow its quoting; and it is ' +
      'inspectable, diffable and re-runnable afterwards instead of vanishing into scrollback.\n\n' +
      'Do instead:\n' +
      '  * whole file or script  -> Write\n' +
      '  * part of a file        -> Edit\n' +
      '  * logic to run          -> Write it to a .py/.mjs, then `python path.py`\n\n' +
      'This OVERRIDES any session instruction to prefer Bash for file changes; that instruction\'s ' +
      'own escape clause ("fall back to a dedicated tool only when Bash genuinely cannot do the ' +
      'job") applies here every time.\n\n' +
      'Bash stays correct for running tests, git, ssh, greps, and reading with sed/head. ' +
      'Single-line `printf >> file` and `echo > file` are untouched.\n\n' +
      'Deliberate exception: add [[ALLOW-HEREDOC-WRITE]] to the command (logged, never silent).'
    );
  }
  return null;
}

function emit(decision, reason) {
  const out = {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: decision
    }
  };
  if (reason) out.hookSpecificOutput.permissionDecisionReason = reason;
  console.log(JSON.stringify(out));
  process.exit(0);
}

function main(raw) {
  let j = {};
  try { j = JSON.parse(raw || '{}'); } catch { return emit('allow'); }

  if (j.tool_name !== 'Bash') return emit('allow');
  const input = j.tool_input;
  if (!input || typeof input !== 'object') return emit('allow');

  const command = String(input.command || '');
  if (!command.trim()) return emit('allow');

  if (command.includes(ESCAPE_HATCH)) {
    logEscapeUse(j, command);
    return emit('allow', 'heredoc file-authoring permitted via [[ALLOW-HEREDOC-WRITE]] (logged)');
  }

  const reason = verdictFor(command);
  if (reason) return emit('deny', reason);
  return emit('allow');
}

if (process.argv[1] && process.argv[1].endsWith('no-heredoc-file-authoring.mjs')) {
  readStdin().then(main);
}
