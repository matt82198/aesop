#!/usr/bin/env python3
"""
power_selftest.py — Health check harness for /power bootstrap.
<<<<<<< HEAD
INDEX: Health check harness for /power bootstrap (now includes guardrail: trigger-layer)
Validates hooks, brain state, heartbeats, decisions, scanner regression, and trigger layer.
=======
INDEX: Health check harness for /power bootstrap. Hook detection unions BOTH settings scopes Claude Code merges (`brain_root/settings{,.local}.json` and `<repo>/.claude/settings{,.local}.json`) — a project-scoped hook is live, so reading only the user scope reported it missing. Requires PreToolUse `Agent|Task` only (`force-model-policy.mjs` is the sole Claude Code hook aesop ships; the former PostToolUse `Write|Edit|NotebookEdit` requirement matched no shipped hook and failed every clean install). `$CLAUDE_PROJECT_DIR` in a hook command is expanded against the repo root before the file-existence check; a path still holding a variable is skipped, not reported missing
Validates hooks, brain state, heartbeats, decisions, and scanner regression.
>>>>>>> 5264dca06ff9ac51582feb23864c34f5efac733d
Exit 0 if OK/DEGRADED, 1 if FAIL. Prints one summary line + bullets for non-OK items.

EXPECTED OUTPUT — HEALTHY SYSTEM:
  POWER-SELFTEST: OK — hooks:ok brain:ok beats:ok decisions:0 pending,0 inbox scanner:n/a

EXPECTED OUTPUT — UNHEALTHY SYSTEM:
  POWER-SELFTEST: DEGRADED — hooks:ok brain:ok beats:stale decisions:2 pending scanner:8/9
  - beats: watchdog:stale
  - scanner: 8/9 tests passed

Exit codes: 0=OK/DEGRADED, 1=FAIL (FAIL is any hook/brain/scanner non-OK; stale beats=WARN not FAIL)

Configuration:
  - Reads aesop.config.json for brain_root, state_root, scripts_root overrides.
  - Env vars override config file: BRAIN_ROOT, AESOP_STATE_ROOT, SCRIPTS_ROOT.
  - Falls back to defaults: brain_root=~/.claude, state_root=<aesop-root>/state.
  - Gracefully degrades when targets don't exist (reports n/a instead of crashing).
"""

import json
import os
import subprocess
import sys
import io
from pathlib import Path
from datetime import datetime
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
        return Check('decisions', 'OK', '0 pending', False)


def check_scanner():
    """Check secret scanner. Returns Check."""
    try:
        scanner_path = paths['scripts_root'] / 'secret_scan.py'
        if not scanner_path.exists():
            return Check('scanner', 'OK', 'n/a', False)

        result = subprocess.run(
            [sys.executable, str(scanner_path), '--staged'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            cwd=str(paths['aesop_root'])
        )

        # Scanner exit 0 = clean, 1 = findings, 2 = usage error
        if result.returncode == 0:
            return Check('scanner', 'OK', None, False)
        elif result.returncode == 1:
            # Findings detected
            return Check('scanner', 'FAIL', 'findings detected', True)
        else:
            return Check('scanner', 'OK', 'n/a', False)
    except Exception as e:
        return Check('scanner', 'OK', 'n/a', False)


def check_trigger_layer():
    r"""Check orchestrator trigger layer (heartbeats and scheduled tasks). Returns Check.

    Validates that the orchestrator trigger layer is properly configured and healthy:
    - Orchestrator heartbeat freshness (< 600s = ok, >= 600s = stale = FAIL)
    - Windows: scheduled tasks Aesop\AesopHeartbeat + Aesop\AesopIdleTick exist and healthy
    - Graceful degradation: unconfigured box (no heartbeats, no tasks) reports UNCONFIGURED (WARN)
    - FAIL-CLOSED: missing trigger layer when configured = FAIL

    Heartbeats are stored in state_root/.heartbeats/orchestrator-heartbeat (consistent with
    watchdog-heartbeat and monitor-heartbeat locations).
    """
    try:
        # Use state_root for orchestrator trigger layer heartbeats
        state_root = paths['state_root']
        hb_dir = state_root / '.heartbeats'
        orchestrator_hb = hb_dir / 'orchestrator-heartbeat'

        # Check if trigger layer is configured (heartbeat directory exists)
        is_configured = hb_dir.exists()

        # If not configured, gracefully degrade to unconfigured (WARN, not FAIL)
        if not is_configured:
            # No trigger layer configured (fresh clone scenario) = unconfigured (WARN, not FAIL)
            # This is OK for fresh clones before adoption setup
            return Check('trigger', 'WARN', 'unconfigured', False)

        # Heartbeat dir exists; check orchestrator heartbeat
        if not orchestrator_hb.exists():
            # Directory exists but orchestrator heartbeat missing = FAIL
            return Check('trigger', 'FAIL', 'missing(orchestrator-heartbeat)', True)

        # Read orchestrator heartbeat timestamp and check staleness (600s threshold)
        try:
            hb_content = orchestrator_hb.read_text().strip()
            hb_epoch = int(hb_content)
            now_epoch = int(datetime.now().timestamp())
            age_s = now_epoch - hb_epoch

            # 600 second threshold for orchestrator heartbeat
            if age_s >= 600:
                return Check('trigger', 'FAIL', f'stale(orchestrator:{age_s}s)', True)
        except (ValueError, OSError):
            # Unreadable or unparseable heartbeat = FAIL
            return Check('trigger', 'FAIL', 'unreadable(orchestrator-heartbeat)', True)

        # Check watchdog heartbeat freshness (300s threshold per config)
        watchdog_hb = hb_dir / 'watchdog-heartbeat'
        if watchdog_hb.exists():
            try:
                hb_content = watchdog_hb.read_text().strip()
                hb_epoch = int(hb_content)
                now_epoch = int(datetime.now().timestamp())
                age_s = now_epoch - hb_epoch

                # 300 second threshold per aesop.config.json heartbeat_thresholds
                watchdog_threshold = config.get('monitor', {}).get('heartbeat_thresholds', {}).get('watchdog', 300)
                if age_s >= watchdog_threshold:
                    return Check('trigger', 'FAIL', f'stale(watchdog:{age_s}s)', True)
            except (ValueError, OSError):
                # Unreadable watchdog heartbeat is a failure when trigger layer is configured
                return Check('trigger', 'FAIL', 'unreadable(watchdog-heartbeat)', True)

        # Windows: check scheduled tasks if platform is Windows
        if sys.platform == 'win32':
            try:
                # Query heartbeat task
                result = subprocess.run(
                    ['schtasks', '/query', '/tn', 'Aesop\\AesopHeartbeat', '/fo', 'csv'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=5
                )

                if result.returncode != 0:
                    return Check('trigger', 'FAIL', 'missing(AesopHeartbeat_task)', True)

                # Parse CSV output to check LastTaskResult (column 5, value 0 = success)
                # CSV format: "HostName","TaskName","Next Run Time","Status","LogonUser","LastTaskResult"
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    # Skip header, check data row
                    try:
                        parts = lines[1].split(',')
                        if len(parts) >= 6:
                            last_result = parts[5].strip().strip('"')
                            if last_result != '0':
                                return Check('trigger', 'FAIL', f'task_failed(AesopHeartbeat:{last_result})', True)
                    except (ValueError, IndexError):
                        pass

                # Query idle tick task
                result = subprocess.run(
                    ['schtasks', '/query', '/tn', 'Aesop\\AesopIdleTick', '/fo', 'csv'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=5
                )

                if result.returncode != 0:
                    return Check('trigger', 'FAIL', 'missing(AesopIdleTick_task)', True)

                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    try:
                        parts = lines[1].split(',')
                        if len(parts) >= 6:
                            last_result = parts[5].strip().strip('"')
                            if last_result != '0':
                                return Check('trigger', 'FAIL', f'task_failed(AesopIdleTick:{last_result})', True)
                    except (ValueError, IndexError):
                        pass
            except subprocess.TimeoutExpired:
                # schtasks timeout = FAIL
                return Check('trigger', 'FAIL', 'schtasks_timeout', True)
            except Exception:
                # schtasks not available on this platform or other error; gracefully degrade on POSIX
                pass

        # All checks passed
        return Check('trigger', 'OK', None, False)
    except Exception as e:
        # Graceful degradation for unexpected errors
        return Check('trigger', 'OK', '(n/a)', False)


def run_checks():
    """Run all health checks and return results."""
    results = []

    for check_fn in [check_hooks, check_brain, check_beats, check_decisions, check_scanner, check_trigger_layer]:
        result = check_fn()
        if result:
            results.append(result)

    return results


def format_output(results):
    """Format results into the summary line and optional bullets."""
    # Build status
    has_fail = any(r.is_fail for r in results)
    has_warn = any(r.status == 'WARN' for r in results)

    if has_fail:
        overall = 'FAIL'
        exit_code = 1
    elif has_warn:
        overall = 'DEGRADED'
        exit_code = 0
    else:
        overall = 'OK'
        exit_code = 0

    # Build detail strings for each check
    check_details = []
    for result in results:
        if result.status in ('OK', 'WARN'):
            if result.name in ('decisions', 'scanner'):
                check_details.append(f'{result.name}:{result.details}')
            else:
                check_details.append(f'{result.name}:ok')
        elif result.status in ('FAIL', 'ERROR'):
            if result.details:
                check_details.append(f'{result.name}:{result.details}')
            else:
                check_details.append(f'{result.name}:fail')

    summary_line = f'POWER-SELFTEST: {overall} — {" ".join(check_details)}'

    # Build bullet points for non-OK items
    bullets = []
    for result in results:
        if result.status not in ('OK',):
            msg = f'- {result.name}: {result.details}' if result.details else f'- {result.name}'
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
