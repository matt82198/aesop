/**
 * ToolingPanel component -- displays aggregated tooling scan results from /api/tooling/summary.
 * Shows a compact card grid with color-coded metrics for TODO count, test coverage,
 * dead code, import cycles, and encoding issues. Includes a refresh button for re-scan.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchApi } from '../lib/api';
import './ToolingPanel.css';

interface ToolingSummary {
  todo_count: number | null;
  coverage_pct: number | null;
  dead_code_count: number | null;
  import_cycle_count: number | null;
  encoding_issues: number | null;
  scanned_at: number | null;
}

type Severity = 'green' | 'yellow' | 'red' | 'unavailable';

function getSeverity(value: number | null, thresholds: { yellow: number; red: number }): Severity {
  if (value === null) return 'unavailable';
  if (value === 0) return 'green';
  if (value >= thresholds.red) return 'red';
  if (value >= thresholds.yellow) return 'yellow';
  return 'green';
}

function getCoverageSeverity(value: number | null): Severity {
  if (value === null) return 'unavailable';
  if (value >= 80) return 'green';
  if (value >= 60) return 'yellow';
  return 'red';
}

function formatValue(value: number | null, suffix?: string): string {
  if (value === null) return 'N/A';
  return `${value}${suffix || ''}`;
}

function formatTimeSince(timestamp: number | null): string {
  if (timestamp === null) return '';
  const seconds = Math.floor(Date.now() / 1000 - timestamp);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

interface MetricCardProps {
  label: string;
  value: string;
  severity: Severity;
  icon: string;
}

function MetricCard({ label, value, severity, icon }: MetricCardProps) {
  return (
    <div className={`tooling-card tooling-card--${severity}`} data-testid={`tooling-card-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="tooling-card__icon" aria-hidden="true">{icon}</div>
      <div className="tooling-card__body">
        <div className="tooling-card__value">{value}</div>
        <div className="tooling-card__label">{label}</div>
      </div>
      <div className={`tooling-card__indicator tooling-card__indicator--${severity}`} />
    </div>
  );
}

export function ToolingPanel() {
  const [summary, setSummary] = useState<ToolingSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>();
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async (force = false) => {
    try {
      const url = force ? '/api/tooling/summary?force=1' : '/api/tooling/summary';
      const data = await fetchApi<ToolingSummary>(url);
      setSummary(data);
      setError(undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tooling data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    loadData(true);
  }, [loadData]);

  if (loading) {
    return <div className="tooling-panel" data-testid="tooling-panel">Loading tooling data...</div>;
  }

  if (error) {
    return (
      <div className="tooling-panel" data-testid="tooling-panel">
        <div className="tooling-panel__error">Error: {error}</div>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="tooling-panel" data-testid="tooling-panel">
        <p>No tooling data available.</p>
      </div>
    );
  }

  return (
    <div className="tooling-panel" data-testid="tooling-panel">
      <div className="tooling-panel__header">
        <h3>Tooling Health</h3>
        <div className="tooling-panel__meta">
          {summary.scanned_at && (
            <span className="tooling-panel__timestamp">
              Scanned {formatTimeSince(summary.scanned_at)}
            </span>
          )}
          <button
            className="tooling-panel__refresh"
            onClick={handleRefresh}
            disabled={refreshing}
            aria-label="Refresh tooling scan"
          >
            {refreshing ? 'Scanning...' : 'Refresh'}
          </button>
        </div>
      </div>
      <div className="tooling-panel__grid">
        <MetricCard
          label="TODOs"
          value={formatValue(summary.todo_count)}
          severity={getSeverity(summary.todo_count, { yellow: 10, red: 50 })}
          icon="[T]"
        />
        <MetricCard
          label="Coverage"
          value={formatValue(summary.coverage_pct, '%')}
          severity={getCoverageSeverity(summary.coverage_pct)}
          icon="[C]"
        />
        <MetricCard
          label="Dead Code"
          value={formatValue(summary.dead_code_count)}
          severity={getSeverity(summary.dead_code_count, { yellow: 5, red: 20 })}
          icon="[D]"
        />
        <MetricCard
          label="Import Cycles"
          value={formatValue(summary.import_cycle_count)}
          severity={getSeverity(summary.import_cycle_count, { yellow: 1, red: 5 })}
          icon="[I]"
        />
        <MetricCard
          label="Encoding Issues"
          value={formatValue(summary.encoding_issues)}
          severity={getSeverity(summary.encoding_issues, { yellow: 1, red: 10 })}
          icon="[E]"
        />
      </div>
    </div>
  );
}
