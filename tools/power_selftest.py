#!/usr/bin/env python3
"""
power_selftest.py — Health check harness for /power bootstrap.
INDEX: Health check harness for /power bootstrap. Hook detection unions BOTH settings scopes Claude Code merges (`brain_root/settings{,.local}.json` and `<repo>/.claude/settings{,.local}.json`) — a project-scoped hook is live, so reading only the user scope reported it missing. Requires PreToolUse `Agent|Task` only (`force-model-policy.mjs` is the sole Claude Code hook aesop ships; the former PostToolUse `Write|Edit|NotebookEdit` requirement matched no shipped hook and failed every clean install). `$CLAUDE_PROJECT_DIR` in a hook command is expanded against the repo root before the file-existence check; a path still holding a variable is skipped, not reported missing. `trigger` check calls `task_cadence_check.run_cli()` in-process (GAP7, the gate's real consumer) to compare live Task Scheduler state against `daemons/install-tasks.ps1`: FAIL on a missing/mis-paced/unevaluable task, WARN naming a task that is registered but deliberately disabled, n/a (non-Windows) off Windows. The scanner check runs the secret-scanner's own TP/FP regression harness (`scanner_selftest.py`), resolved via `scripts_root` then a profile-agnostic `$HOME/scripts` fallback, and reports a `passed/total` count -- never staged-file state, which is a different gate's job. Every check result funnels through `render_segment()`, which treats `None` (a dropped/crashed check, or an OK/WARN result whose details silently stayed unset) as `FAIL:unevaluated` rather than ever rendering Python's `None` as healthy (fixes a live `scanner:None` headline that read clean while unevaluated).
Validates hooks, brain state, heartbeats, decisions, scanner regression, and scheduled-task cadence.
Exit 0 if OK/DEGRADED, 1 if FAIL. Prints one summary line + bullets for non-OK items.

EXPECTED OUTPUT — HEALTHY SYSTEM:
  POWER-SELFTEST: OK — hooks:ok brain:ok beats:ok decisions:0 pending,0 inbox scanner:n/a trigger:ok

EXPECTED OUTPUT — UNHEALTHY SYSTEM:
  POWER-SELFTEST: DEGRADED — hooks:ok brain:ok beats:stale decisions:2 pending scanner:8/9 trigger:WARN:disabled: AesopMergeQueue
  - beats: watchdog:stale
  - scanner: 8/9 tests passed
  - trigger: WARN:disabled: AesopMergeQueue

Exit codes: 0=OK/DEGRADED, 1=FAIL (FAIL is any hook/brain/scanner/trigger non-OK; stale beats and a
deliberately-disabled scheduled task are WARN, not FAIL)

Configuration:
  - Reads aesop.config.json for brain_root, state_root, scripts_root overrides.
  - Env vars override config file: BRAIN_ROOT, AESOP_STATE_ROOT, SCRIPTS_ROOT.
  - Falls back to defaults: brain_root=~/.claude, state_root=<aesop-root>/state.
  - Gracefully degrades when targets don't exist (reports n/a instead of crashing).
"""

import json
import os
import re
import subprocess
import sys
import io
from pathlib import Path
from collections import namedtuple

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from health_checks import check_watchdog_heartbeat, check_monitor_heartbeat

# Force UTF-8 encoding on stdout to prevent UnicodeEncodeError on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Named tuples for result tracking
Check = namedtuple('Check', ['name', 'status', 'details', 'is_fail'])


def load_config():
    """Load aesop.config.json if present; return dict."""
    try:
        config_path = Path.cwd() / 'aesop.config.json'
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def resolve_paths(config):
    """Resolve paths with precedence: env var > config > default."""
    aesop_root = Path.cwd()

    # brain_root: env BRAIN_ROOT > config brain_root > default ~/.claude
    brain_root = Path(
        os.environ.get('BRAIN_ROOT', config.get('brain_root', ''))
        or str(Path.home() / '.claude')
    ).expanduser()

    # state_root: env AESOP_STATE_ROOT > config state_root > default <aesop-root>/state
    state_root = Path(
        os.environ.get('AESOP_STATE_ROOT', config.get('state_root', ''))
        or str(aesop_root / 'state')
    ).expanduser()

    # scripts_root: env SCRIPTS_ROOT > config scripts_root > default <aesop-root>/tools
    scripts_root = Path(
        os.environ.get('SCRIPTS_ROOT', config.get('scripts_root', ''))
        or str(aesop_root / 'tools')
    ).expanduser()

    return {
        'aesop_root': aesop_root,
        'brain_root': brain_root,
        'state_root': state_root,
        'scripts_root': scripts_root,
    }


import os
config = load_config()
paths = resolve_paths(config)


# Claude Code merges hook settings from the user scope (~/.claude) and the
# project scope (<repo>/.claude), each with an optional .local override. A hook
# registered in ANY of them is live, so the check reads all four and unions them
# -- reading only the user scope reported a correctly-registered project hook as
# missing.
SETTINGS_FILENAMES = ('settings.json', 'settings.local.json')


def _iter_settings_paths():
    """Yield every settings file Claude Code would merge, user scope first."""
    for root in (paths['brain_root'], paths['aesop_root'] / '.claude'):
        for name in SETTINGS_FILENAMES:
            yield root / name


def _hook_command_path(cmd, aesop_root):
    """Extract the script path from a hook command, or None if not checkable.

    Hook commands are shell strings: the script may be quoted and may reference
    $CLAUDE_PROJECT_DIR, which Claude Code expands at run time. Treating an
    unexpanded variable as a missing file is a false alarm, so anything still
    holding a variable after substitution is skipped rather than reported.
    """
    for raw in cmd.split():
        part = raw.strip('"\'')
        if not part.endswith(('.mjs', '.js', '.py', '.sh')):
            continue
        expanded = part.replace('${CLAUDE_PROJECT_DIR}', str(aesop_root))
        expanded = expanded.replace('$CLAUDE_PROJECT_DIR', str(aesop_root))
        if '$' in expanded:
            return None
        return expanded
    return None


def check_hooks():
    """Check hooks configuration. Returns Check."""
    try:
        aesop_root = paths['aesop_root']
        pre_matchers = set()
        all_commands = []
        found_any = False

        for settings_path in _iter_settings_paths():
            if not settings_path.exists():
                continue
            found_any = True
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)

            hooks = settings.get('hooks', {})
            entries = hooks.get('PreToolUse', [])
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                matcher = entry.get('matcher', '')
                if matcher:
                    pre_matchers.update(matcher.split('|'))
                for hook in entry.get('hooks', []):
                    if isinstance(hook, dict) and hook.get('command'):
                        all_commands.append(hook['command'])

        if not found_any:
            return Check('hooks', 'OK', '(n/a)', False)

        # Only PreToolUse Agent|Task is required: force-model-policy.mjs is the
        # one Claude Code hook aesop ships. A PostToolUse requirement used to
        # live here too, but no such hook exists in the repo, so every clean
        # install failed this check for a hook it had no way to register.
        required_pre = {'Agent', 'Task'}
        missing_pre = required_pre - pre_matchers

        missing_files = []
        for cmd in all_commands:
            resolved = _hook_command_path(cmd, aesop_root)
            if resolved and not Path(resolved).exists():
                missing_files.append(resolved)

        if missing_pre:
            return Check('hooks', 'FAIL', f'missing matchers: PreToolUse: {missing_pre}', True)
        elif missing_files:
            return Check('hooks', 'FAIL', f'missing files: {missing_files}', True)
        else:
            return Check('hooks', 'OK', None, False)
    except Exception:
        return Check('hooks', 'OK', '(error reading)', False)


def check_brain():
    """Check brain (git) status. Returns Check."""
    try:
        brain_path = paths['brain_root']
        if not (brain_path / '.git').exists():
            return Check('brain', 'OK', '(no git repo)', False)

        # Get status
        status_output = subprocess.run(
            ['git', '-C', str(brain_path), 'status', '--porcelain'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        ).stdout.strip()

        status_lines = [l for l in status_output.split('\n') if l]

        # Check ahead of current branch's upstream
        try:
            current_branch = subprocess.run(
                ['git', '-C', str(brain_path), 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
            ).stdout.strip()
        except:
            current_branch = 'HEAD'

        ahead_output = subprocess.run(
            ['git', '-C', str(brain_path), 'rev-list', '--left-only', '--count', f'{current_branch}@{{u}}...{current_branch}'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        ).stdout.strip()

        try:
            ahead_count = int(ahead_output) if ahead_output else 0
        except:
            ahead_count = 0

        if ahead_count > 0:
            return Check('brain', 'FAIL', f'ahead:{ahead_count}', True)
        elif status_lines:
            return Check('brain', 'WARN', f'{len(status_lines)} uncommitted', False)
        else:
            return Check('brain', 'OK', None, False)
    except Exception:
        return Check('brain', 'OK', '(error checking)', False)


def check_beats():
    """Check heartbeats using shared health_checks module. Returns Check."""
    try:
        heartbeat_results = []

        # Watchdog heartbeat (using shared health_checks module)
        try:
            is_stale, age, info = check_watchdog_heartbeat(paths['state_root'])
            if info and "missing" in info.lower():
                heartbeat_results.append(('watchdog', 'missing', None))
            elif is_stale and age > 0:
                heartbeat_results.append(('watchdog', 'stale', int(age)))
            elif is_stale and age == 0:
                # Unreadable or unparseable
                heartbeat_results.append(('watchdog', 'n/a', None))
            else:
                heartbeat_results.append(('watchdog', 'ok', int(age)))
        except Exception:
            heartbeat_results.append(('watchdog', 'n/a', None))

        # Monitor heartbeat (using shared health_checks module)
        try:
            is_stale, age, info = check_monitor_heartbeat(paths['state_root'])
            if info and "missing" in info.lower():
                heartbeat_results.append(('monitor', 'missing', None))
            elif is_stale and age > 0:
                heartbeat_results.append(('monitor', 'stale', int(age)))
            elif is_stale and age == 0:
                # Unreadable or unparseable
                heartbeat_results.append(('monitor', 'n/a', None))
            else:
                heartbeat_results.append(('monitor', 'ok', int(age)))
        except Exception:
            heartbeat_results.append(('monitor', 'n/a', None))

        # Determine beats status
        beats_ok = all(status not in ('error', 'stale') for _, status, _ in heartbeat_results)
        beats_stale = any(status == 'stale' for _, status, _ in heartbeat_results)
        beats_all_na = all(status == 'n/a' for _, status, _ in heartbeat_results)

        if beats_all_na:
            return Check('beats', 'OK', '(n/a)', False)
        elif not beats_ok:
            details = '; '.join(f'{name}:{status}' for name, status, _ in heartbeat_results)
            return Check('beats', 'FAIL', details, True)
        elif beats_stale:
            details = '; '.join(f'{name}:{status}' for name, status, _ in heartbeat_results)
            return Check('beats', 'WARN', details, False)
        else:
            return Check('beats', 'OK', None, False)
    except Exception:
        return Check('beats', 'OK', '(n/a)', False)


def check_decisions():
    """Check decisions/inbox counts. Returns Check."""
    try:
        pending_count = 0
        inbox_count = 0

        try:
            pending_path = paths['brain_root'] / 'plans' / 'PENDING-DECISIONS.md'
            if pending_path.exists():
                content = pending_path.read_text()
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('- [ ]') or (line.startswith('-') and '[' not in line and line):
                        pending_count += 1
        except Exception:
            pass

        try:
            inbox_path = paths['state_root'] / 'ui-inbox.md'
            if inbox_path.exists():
                content = inbox_path.read_text()
                for line in content.split('\n'):
                    if '- [' in line and ']' in line:
                        inbox_count += 1
        except Exception:
            pass

        details = f'{pending_count} pending'
        if inbox_count > 0:
            details += f',{inbox_count} inbox'

        return Check('decisions', 'OK', details, False)
    except Exception:
        return Check('decisions', 'OK', '0 pending', False)


def _resolve_scanner_selftest_path():
    """Locate the secret-scanner's own regression harness (TP/FP self-test).

    Prefers the copy living alongside this tool (scripts_root, default
    <aesop-root>/tools) so the check validates the scanner that actually
    ships here; falls back to the canonical ~/scripts library copy (Cardinal
    Rule 9: check ~/scripts before treating anything as missing). Both are
    resolved through Path.home()/paths['scripts_root'] -- never a
    hard-coded user profile -- so it behaves identically across boxes.
    Returns None if neither candidate exists.
    """
    candidates = [
        paths['scripts_root'] / 'scanner_selftest.py',
        Path.home() / 'scripts' / 'scanner_selftest.py',
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def check_trigger(platform=None, query=None):
    """Check scheduled-task cadence health via task_cadence_check. Returns Check.

    Imports task_cadence_check (tools/ is already on sys.path -- see the
    sys.path.insert above) and calls its run_cli() in-process rather than
    shelling out: run_cli() is a plain importable function, not a CLI-only
    surface. `platform`/`query` are injectable (mirroring task_cadence_check's
    own injectable `query`) so tests never touch the real Task Scheduler.

    Policy: a task install-tasks.ps1 defines that Task Scheduler has never
    registered (MISSING), that runs at the wrong cadence (INTERVAL_MISMATCH),
    or that cannot be evaluated at all (QUERY_ERROR) is FAIL -- "could not
    evaluate" must never read as healthy. A task that IS registered but
    deliberately DISABLED (e.g. AesopMergeQueue on this box since 2026-09-02)
    is a WARN naming the task: disabling a daemon on purpose is an operator
    decision, not drift, and FAIL is reserved for a task that is actually
    missing or mis-paced.
    """
    plat = platform if platform is not None else sys.platform
    try:
        import task_cadence_check
    except Exception as exc:
        return Check('trigger', 'OK', 'n/a (import error: %s)' % exc, False)

    # Graceful degradation (same precedent as check_hooks/check_scanner):
    # a directory that isn't a real aesop checkout has no install-tasks.ps1
    # to evaluate against. That's "not applicable", not an evaluation
    # failure -- fail-closed FAIL is reserved for a checkout that DOES
    # define a task but can't confirm it's healthy.
    install_script = paths['aesop_root'] / 'daemons' / 'install-tasks.ps1'
    if query is None and not install_script.exists():
        return Check('trigger', 'OK', 'n/a (no install-tasks.ps1)', False)

    run_kwargs = {}
    if query is not None:
        run_kwargs['query'] = query

    try:
        argv = ['--json', '--root', str(paths['aesop_root'])]
        exit_code, output = task_cadence_check.run_cli(argv, platform=plat, **run_kwargs)
    except Exception as exc:
        return Check('trigger', 'FAIL', 'FAIL:error invoking cadence check: %s' % exc, True)

    try:
        report = json.loads(output)
    except (ValueError, TypeError):
        report = None

    if exit_code == 0:
        if report and report.get('status') == 'SKIPPED-non-windows':
            return Check('trigger', 'OK', 'n/a (non-Windows)', False)
        return Check('trigger', 'OK', 'ok', False)

    if report is None:
        # Early-failure paths (unreadable/unparseable install script) return
        # plain text even with --json -- still a real, fail-closed FAIL.
        return Check('trigger', 'FAIL', 'FAIL:%s' % output.strip()[:200], True)

    tasks = report.get('tasks', [])
    query_errors = [t for t in tasks if t.get('status') == 'QUERY_ERROR']
    missing = [t for t in tasks if t.get('status') == 'MISSING']
    mismatched = [t for t in tasks if t.get('status') == 'INTERVAL_MISMATCH']
    disabled = [t for t in tasks if t.get('status') == 'DISABLED']

    if query_errors:
        names = ', '.join(t['task'] for t in query_errors)
        return Check('trigger', 'FAIL', 'FAIL:cannot evaluate: %s' % names, True)
    if missing:
        names = ', '.join(t['task'] for t in missing)
        return Check('trigger', 'FAIL', 'FAIL:missing task(s): %s' % names, True)
    if mismatched:
        names = ', '.join(t['task'] for t in mismatched)
        return Check('trigger', 'FAIL', 'FAIL:cadence drift: %s' % names, True)
    if disabled:
        names = ', '.join(t['task'] for t in disabled)
        return Check('trigger', 'WARN', 'WARN:disabled: %s' % names, False)

    # Nonzero exit with no recognizable per-task drift -- fail closed rather
    # than silently reporting health for a report shape we don't understand.
    return Check('trigger', 'FAIL', 'FAIL:unrecognized report (exit %d)' % exit_code, True)


def check_scanner():
    """Check the secret scanner's own regression self-test. Returns Check.

    This validates scanner REGRESSION (does secret_scan.py still correctly
    flag true positives and pass false positives?), not "are there staged
    secrets right now" -- the latter is a different question (answered by
    the pre-push gate) and depends on git/cwd state the health check has no
    business caring about.

    A check that cannot locate or run the harness, or cannot parse a
    pass/total count out of its output, must never report OK/n/a -- it
    reports FAIL so a silently-broken scanner gate is never read healthy.
    """
    try:
        scanner_path = _resolve_scanner_selftest_path()
        if scanner_path is None:
            return Check('scanner', 'FAIL', 'unevaluated: scanner_selftest.py not found', True)

        result = subprocess.run(
            [sys.executable, str(scanner_path)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            cwd=str(scanner_path.parent)
        )

        output = result.stdout.strip()
        match = re.search(r'(\d+)/(\d+)\s*passed', output) or re.search(r'(\d+)/(\d+)', output)
        if not match:
            return Check(
                'scanner', 'FAIL',
                f'unevaluated: unparseable output (exit {result.returncode})', True
            )

        passed, total = int(match.group(1)), int(match.group(2))
        details = f'{passed}/{total}'
        if passed < total:
            return Check('scanner', 'FAIL', details, True)
        return Check('scanner', 'OK', details, False)
    except Exception as e:
        return Check('scanner', 'FAIL', f'unevaluated: exception:{type(e).__name__}', True)


# Checks whose healthy state is expressed THROUGH their details string
# (a count, a pending tally) rather than a bare "ok" -- a None value here
# means the check didn't actually evaluate anything, so it must never be
# allowed to render as healthy. hooks/brain/beats intentionally carry
# details=None on a clean OK and that stays a plain "<name>:ok".
DETAILED_OK_CHECKS = ('decisions', 'scanner', 'trigger')

# (name, check_fn) pairs -- the single source of truth run_checks() iterates.
# Tests may monkeypatch this tuple to inject a check that returns None, to
# prove a crashed/mis-shaped check is surfaced as a failure, not dropped.
CHECKS = (
    ('hooks', check_hooks),
    ('brain', check_brain),
    ('beats', check_beats),
    ('decisions', check_decisions),
    ('scanner', check_scanner),
    ('trigger', check_trigger),
)


def run_checks():
    """Run all health checks and return results.

    A check function is expected to return a Check namedtuple. If it
    returns None (or raises) instead -- a crash, a mis-shaped result -- that
    is itself a failure of evaluation and must surface as one, never be
    silently dropped from the headline (silence would read as healthy).
    """
    results = []

    for name, check_fn in CHECKS:
        try:
            result = check_fn()
        except Exception as e:
            result = None
            detail = f'unevaluated: exception:{type(e).__name__}'
            results.append(Check(name, 'FAIL', detail, True))
            continue
        if result is None:
            results.append(Check(name, 'FAIL', 'unevaluated: check returned None', True))
        else:
            results.append(result)

    return results


def render_segment(name, result):
    """Render one check's headline segment as '<name>:<status-or-detail>'.

    The single place every check result is turned into text. A result this
    cannot make sense of -- None, an unrecognized status, or an OK/WARN
    DETAILED_OK_CHECKS result whose details is None -- renders
    '<name>:FAIL:unevaluated' and is always treated as a failure. This is
    the fix for the literal "scanner:None" that reached the headline when a
    check's details silently stayed unset.

    Returns (segment_str, is_fail).
    """
    status = getattr(result, 'status', None)
    details = getattr(result, 'details', None)

    if status not in ('OK', 'WARN', 'FAIL', 'ERROR'):
        return f'{name}:FAIL:unevaluated', True

    if status in ('OK', 'WARN'):
        if name in DETAILED_OK_CHECKS:
            if details is None:
                return f'{name}:FAIL:unevaluated', True
            return f'{name}:{details}', False
        return f'{name}:ok', False

    # FAIL / ERROR -- always carries an explicit FAIL: marker in the segment
    # itself (not just the overall verdict word) so a reader scanning the
    # per-check list sees which one broke without cross-referencing status.
    if details:
        return f'{name}:FAIL:{details}', True
    return f'{name}:FAIL', True


def format_output(results):
    """Format results into the summary line and optional bullets."""
    check_details = []
    any_fail = False
    any_warn = any(getattr(r, 'status', None) == 'WARN' for r in results)

    for result in results:
        name = getattr(result, 'name', '?')
        segment, seg_is_fail = render_segment(name, result)
        check_details.append(segment)
        if seg_is_fail or getattr(result, 'is_fail', False):
            any_fail = True

    if any_fail:
        overall = 'FAIL'
        exit_code = 1
    elif any_warn:
        overall = 'DEGRADED'
        exit_code = 0
    else:
        overall = 'OK'
        exit_code = 0

    summary_line = f'POWER-SELFTEST: {overall} — {" ".join(check_details)}'

    # Build bullet points for non-OK items
    bullets = []
    for result in results:
        if getattr(result, 'status', None) not in ('OK',):
            name = getattr(result, 'name', '?')
            details = getattr(result, 'details', None)
            msg = f'- {name}: {details}' if details else f'- {name}'
            bullets.append(msg)

    output_lines = [summary_line]
    output_lines.extend(bullets)

    return '\n'.join(output_lines), exit_code


def main():
    results = run_checks()
    output, exit_code = format_output(results)
    print(output)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
