/**
 * HealthHeader — Mission-Control status header (always visible, D4).
 *
 * Status-first 3-zone layout:
 *   Zone 1 (fleet):   orchestrator phase/activity · agents count · running/idle/issues
 *                      status-badge breakdown (derived from the live `agents` array)
 *   Zone 2 (system):  watchdog · monitor · alerts · SSE connection · data freshness
 *   Zone 3 (controls): cost snapshot (real /api/cost data, honest empty-state when
 *                      no pricing is configured) · theme toggle · manual refresh
 *
 * Every metric is bound to a real field already flowing through App.tsx's SSE
 * state — nothing here is invented. Where the backend has no endpoint for a
 * metric (e.g. CI state of main), it is simply omitted rather than faked.
 *
 * Every cell is a clickable element jumping to its corresponding view (#/overview, #/activity, etc).
 * Props driven by App.tsx; no local state beyond focus/hover.
 */

import { useCallback } from 'react';
import type {
  HeartbeatStatus,
  Agent,
  OrchestratorStatus,
  Alert,
  SSEConnectionStatus,
  CostSummary,
} from '../lib/types';
import { TESTIDS } from '../test/fixtures';
import './HealthHeader.css';

interface HealthHeaderProps {
  watchdog: HeartbeatStatus | null;
  monitor: HeartbeatStatus | null;
  orchestrator: OrchestratorStatus | null;
  agents: Agent[] | null;
  alerts: Alert | null;
  cost?: CostSummary | null;
  connectionStatus: SSEConnectionStatus;
  dataTimestamp?: number | null; // Epoch ms when last SSE payload was received
  heartbeatTimestamp?: number | null; // Epoch ms when last heartbeat was received (wave-20 liveness)
  now?: number; // Wall-clock time for staleness re-evaluation (updated ~5s)
  onThemeToggle: () => void;
  onRefresh: () => void;
}

/**
 * Bucket a raw agent status into one of the three headline groups.
 * 'running'/'idle' pass through; every other value (SUSPICIOUS/HIGH/DRIFT/MED/
 * anything unrecognized) is treated as an issue worth surfacing.
 */
function bucketAgentStatus(status: string): 'running' | 'idle' | 'issues' {
  if (status === 'running') return 'running';
  if (status === 'idle') return 'idle';
  return 'issues';
}

function computeAgentCounts(agents: Agent[] | null): { running: number; idle: number; issues: number } {
  const counts = { running: 0, idle: 0, issues: 0 };
  (agents ?? []).forEach((a) => {
    counts[bucketAgentStatus(a.status)] += 1;
  });
  return counts;
}

/**
 * Sum estimated dollar cost across all models from GET /api/cost.
 * Returns null (not 0) when pricing isn't configured or no cost data has
 * arrived yet — the caller renders an honest "n/a" rather than a fake $0.00.
 */
function computeCostSnapshot(cost: CostSummary | null | undefined): number | null {
  if (!cost || !cost.has_pricing) return null;
  const values = Object.values(cost.estimates_by_model);
  if (values.length === 0) return null;
  return values.reduce((sum, e) => sum + (e.total_cost || 0), 0);
}

/**
 * Format age in seconds as a readable duration.
 */
function formatAge(ageSeconds: number): string {
  if (ageSeconds < 0) return 'unknown';
  if (ageSeconds < 60) return `${ageSeconds}s`;
  const minutes = Math.floor(ageSeconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(ageSeconds / 3600);
  return `${hours}h`;
}

/**
 * Format a timestamp as a relative time string (e.g., "1m ago", "15s ago").
 */
function formatRelativeTime(epochMs: number): string {
  const ageMs = Date.now() - epochMs;
  const ageSecs = Math.floor(ageMs / 1000);

  if (ageSecs < 60) return `${ageSecs}s ago`;
  const mins = Math.floor(ageSecs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(ageSecs / 3600);
  return `${hours}h ago`;
}

export function HealthHeader({
  watchdog,
  monitor,
  orchestrator,
  agents,
  alerts,
  cost,
  connectionStatus,
  dataTimestamp,
  heartbeatTimestamp,
  now,
  onThemeToggle,
  onRefresh,
}: HealthHeaderProps) {
  const handleWatchdogClick = useCallback(() => {
    window.location.hash = '#/activity';
  }, []);

  const handleMonitorClick = useCallback(() => {
    window.location.hash = '#/activity';
  }, []);

  const handleOrchestratorClick = useCallback(() => {
    window.location.hash = '#/activity';
  }, []);

  const handleAlertsClick = useCallback(() => {
    window.location.hash = '#/';
  }, []);

  // Determine audit phase badge — the status file signals via phase/activity, not role
  const isAuditPhase =
    orchestrator?.orchestrators.some(
      (o) => o.phase?.toLowerCase().includes('audit') || o.activity?.toLowerCase().includes('audit'),
    ) ?? false;
  const orchestratorActivity =
    orchestrator?.orchestrators.map((o) => o.activity || o.phase).filter(Boolean)[0] ?? 'no active session';

  const agentsCount = agents?.length ?? 0;
  const alertsCount = alerts?.count ?? 0;
  const agentCounts = computeAgentCounts(agents);
  const costTotal = computeCostSnapshot(cost);

  // Compute staleness: data stale if > 60s old, OR heartbeat stale if > 60s old (wave-20 liveness)
  // Use wall-clock 'now' if available for proper re-evaluation without SSE traffic
  const currentTime = now ?? Date.now();
  const dataAgeMs = dataTimestamp ? currentTime - dataTimestamp : -1;
  const heartbeatAgeMs = heartbeatTimestamp ? currentTime - heartbeatTimestamp : -1;

  // Stale if either data or heartbeat exceeds 60s (or if heartbeat exists but data doesn't)
  const isDataStale = dataAgeMs > 60000 || (heartbeatAgeMs >= 0 && heartbeatAgeMs > 60000);
  const dataTimeStr = dataTimestamp ? formatRelativeTime(dataTimestamp) : 'unknown';
  const stalenessAge = isDataStale ? formatAge(Math.floor(Math.max(dataAgeMs, heartbeatAgeMs) / 1000)) : null;

  // Determine freshness status for visual indicator (wave-35 UX improvement)
  // Green: < 20s, Yellow: 20-60s, Red: > 60s
  const freshnessDotStatus = isDataStale ? 'stale' : dataAgeMs < 20000 ? 'fresh' : 'aging';

  // Determine max severity for alerts color
  let maxAlertSeverity = 'neutral';
  if (alertsCount > 0 && alerts?.lines.length) {
    const firstLine = alerts.lines[0] || '';
    if (firstLine.includes('HIGH') || firstLine.includes('SUSPICIOUS')) {
      maxAlertSeverity = 'error';
    } else if (firstLine.includes('MED') || firstLine.includes('DRIFT')) {
      maxAlertSeverity = 'warn';
    }
  }

  return (
    <header className="health-header" data-testid={TESTIDS.healthHeader} role="banner">
      <div className="health-header__cells">
        {/* Zone 1: fleet — orchestrator phase/activity + agent status breakdown */}
        <div className="health-header__zone health-header__zone--fleet" data-testid={`${TESTIDS.healthZone}-fleet`}>
          {/* Orchestrator cell */}
          <button
            type="button"
            className="health-header__cell health-header__cell--orchestrator"
            data-testid={TESTIDS.healthOrchestrator}
            onClick={handleOrchestratorClick}
            aria-label="Orchestrator status"
          >
            <span className="health-header__label">Orchestrator</span>
            {isAuditPhase && (
              <span className="health-header__badge" role="status">
                Audit
              </span>
            )}
            <span className="health-header__status">{orchestratorActivity}</span>
          </button>

          {/* Agents count */}
          <button
            type="button"
            className="health-header__cell health-header__cell--agents"
            data-testid={TESTIDS.healthAgentsCount}
            onClick={handleAlertsClick}
            aria-label={`${agentsCount} agents running`}
          >
            <span className="health-header__label">Agents</span>
            <span className="health-header__count">{agentsCount}</span>
          </button>

          {/* Agent status breakdown badges — derived from the live agents array */}
          <div className="health-header__badge-group" role="group" aria-label="Agent status breakdown">
            <span
              className="health-header__status-badge"
              data-status="running"
              data-testid={TESTIDS.healthAgentsRunning}
            >
              <span className="health-header__status-badge-dot" aria-hidden="true" />
              <span className="health-header__status-badge-count">{agentCounts.running}</span>
              <span className="health-header__status-badge-label">Running</span>
            </span>
            <span
              className="health-header__status-badge"
              data-status="idle"
              data-testid={TESTIDS.healthAgentsIdle}
            >
              <span className="health-header__status-badge-dot" aria-hidden="true" />
              <span className="health-header__status-badge-count">{agentCounts.idle}</span>
              <span className="health-header__status-badge-label">Idle</span>
            </span>
            <span
              className="health-header__status-badge"
              data-status="issues"
              data-empty={agentCounts.issues === 0 ? 'true' : 'false'}
              data-testid={TESTIDS.healthAgentsIssues}
            >
              <span className="health-header__status-badge-dot" aria-hidden="true" />
              <span className="health-header__status-badge-count">{agentCounts.issues}</span>
              <span className="health-header__status-badge-label">Warnings</span>
            </span>
          </div>
        </div>

        {/* Zone 2: system — watchdog / monitor / alerts / SSE / data freshness */}
        <div className="health-header__zone health-header__zone--system" data-testid={`${TESTIDS.healthZone}-system`}>
          {/* Watchdog cell */}
          <button
            type="button"
            className="health-header__cell health-header__cell--watchdog"
            data-testid={TESTIDS.healthWatchdog}
            onClick={handleWatchdogClick}
            aria-label={`Watchdog: ${watchdog?.alive ?? 'unknown'} (age: ${formatAge(watchdog?.age ?? -1)})`}
          >
            <span className="health-header__label">Watchdog</span>
            <span
              className={`health-header__status text-status-${
                watchdog?.alive === 'ALIVE'
                  ? 'ok'
                  : watchdog?.alive === 'STALE'
                    ? 'error'
                    : 'neutral'
              }`}
            >
              {watchdog?.alive ?? 'unknown'}
              {watchdog && watchdog.age >= 0 && ` +${formatAge(watchdog.age)}`}
            </span>
          </button>

          {/* Monitor cell */}
          <button
            type="button"
            className="health-header__cell health-header__cell--monitor"
            data-testid={TESTIDS.healthMonitor}
            onClick={handleMonitorClick}
            aria-label={`Monitor: ${monitor?.alive ?? 'unknown'}`}
          >
            <span className="health-header__label">Monitor</span>
            <span
              className={`health-header__status text-status-${
                monitor?.alive === 'ALIVE' ? 'ok' : 'neutral'
              }`}
            >
              {monitor?.alive ?? 'unknown'}
            </span>
          </button>

          {/* Alerts count */}
          <button
            type="button"
            className={`health-header__cell health-header__cell--alerts`}
            data-testid={TESTIDS.healthAlertsCount}
            onClick={handleAlertsClick}
            aria-label={`${alertsCount} alerts${alertsCount > 0 ? ': ' + maxAlertSeverity.toUpperCase() : ''}`}
          >
            <span className="health-header__label">Alerts</span>
            <span
              className={`health-header__count ${
                alertsCount > 0
                  ? maxAlertSeverity === 'error'
                    ? 'text-status-error'
                    : maxAlertSeverity === 'warn'
                      ? 'text-status-warn'
                      : 'text-status-info'
                  : ''
              }`}
            >
              {alertsCount}
            </span>
          </button>

          {/* Data timestamp */}
          <div className="health-header__data-wrapper">
            <span
              className={`health-header__cell health-header__cell--timestamp ${
                isDataStale ? 'health-header__cell--stale' : ''
              }`}
              data-testid="health-data-timestamp"
              role="status"
              aria-live="polite"
              aria-label={`Data as of ${dataTimeStr}${isDataStale ? ` (stale by ${stalenessAge})` : ''}`}
            >
              <span className="health-header__label">Data</span>
              <div className="health-header__status-row">
                <span className={`health-header__freshness-dot health-header__freshness-dot--${freshnessDotStatus}`} data-testid="health-freshness-dot" aria-hidden="true" />
                <span className={`health-header__status ${isDataStale ? 'text-status-warn' : 'text-status-ok'}`}>
                  {dataTimeStr}
                </span>
              </div>
            </span>
            {isDataStale && (
              <div
                className="health-header__warning-strip"
                role="alert"
                aria-label={`Data is stale: ${stalenessAge} old`}
              >
                <span className="health-header__warning-icon">⚠</span>
                <span className="health-header__warning-text">Data stale: {stalenessAge} old</span>
              </div>
            )}
          </div>

          {/* SSE status */}
          <span
            className="health-header__cell health-header__cell--sse"
            data-testid={TESTIDS.sseStatus}
            data-status={connectionStatus.status}
            role="status"
            aria-live="polite"
            aria-label={`Connection: ${connectionStatus.status}`}
          >
            <span className="health-header__label">SSE</span>
            <span
              className={`health-header__status text-status-${
                connectionStatus.status === 'live'
                  ? 'ok'
                  : connectionStatus.status === 'reconnecting'
                    ? 'warn'
                    : 'error'
              }`}
            >
              {connectionStatus.status === 'live'
                ? 'Live'
                : connectionStatus.status === 'reconnecting'
                  ? 'Reconnecting'
                  : 'Error'}
            </span>
          </span>
        </div>

        {/* Zone 3: controls — cost snapshot (real /api/cost), theme, refresh */}
        <div className="health-header__zone health-header__zone--controls" data-testid={`${TESTIDS.healthZone}-controls`}>
          <div
            className="health-header__cell health-header__cell--cost"
            data-testid={TESTIDS.healthCost}
            role="status"
            aria-label={costTotal !== null ? `Cost snapshot: $${costTotal.toFixed(2)}` : 'Cost snapshot: no data yet'}
          >
            <span className="health-header__label">Cost</span>
            <span className="health-header__status">
              {costTotal !== null ? `$${costTotal.toFixed(2)}` : 'n/a — no runs yet'}
            </span>
          </div>

          {/* Theme toggle */}
          <button
            type="button"
            className="health-header__cell health-header__cell--theme"
            data-testid={TESTIDS.themeToggle}
            onClick={onThemeToggle}
            aria-label="Toggle color theme"
          >
            <span className="health-header__label">Theme</span>
            <span className="health-header__icon">◐</span>
          </button>

          {/* Refresh button */}
          <button
            type="button"
            className="health-header__cell health-header__cell--refresh"
            data-testid={TESTIDS.refreshButton}
            onClick={onRefresh}
            aria-label="Refresh data"
          >
            <span className="health-header__label">Refresh</span>
            <span className="health-header__icon">↻</span>
          </button>
        </div>
      </div>
    </header>
  );
}
