/**
 * FirstTryRateBoard — First-try success board (C3).
 *
 * Shows % of dispatches that needed no repair, broken down by domain and lane.
 * Computed from analysis of all agent transcripts; displays as a dashboard board
 * with domain cards, lane cards, and an overall metric.
 *
 * Used as a standalone view (can be embedded in Activity or Cost views);
 * lazy-fetches first-try rate data on demand.
 */

import { useEffect, useState } from 'react';
import type { FirstTryRateBoard as FirstTryRateBoardType, FirstTryStats } from '../lib/types';
import { fetchAPI } from '../lib/api';
import './FirstTryRateBoard.css';

interface FirstTryRateBoardProps {
  autoRefresh?: number; // refresh interval in seconds (0 = no auto-refresh)
}

export function FirstTryRateBoard({ autoRefresh = 0 }: FirstTryRateBoardProps) {
  const [data, setData] = useState<FirstTryRateBoardType | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAPI('/api/quality/first-try-rate');
      if (result.error) {
        setError(result.error);
      } else {
        setData(result as FirstTryRateBoardType);
        setLastUpdate(new Date());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch first-try rate');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();

    if (autoRefresh <= 0) return;

    const interval = setInterval(fetchData, autoRefresh * 1000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  if (error) {
    return (
      <div className="first-try-board error" data-testid="first-try-board-error">
        <p>Error loading first-try rates: {error}</p>
        <button onClick={fetchData}>Retry</button>
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="first-try-board loading" data-testid="first-try-board-loading">
        <p>Loading first-try success board...</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="first-try-board empty" data-testid="first-try-board-empty">
        <p>No first-try rate data available</p>
      </div>
    );
  }

  return (
    <div className="first-try-board" data-testid="first-try-board">
      <div className="board-header">
        <h2>First-Try Success Rate</h2>
        <div className="board-controls">
          <button onClick={fetchData} className="refresh-btn" title="Refresh">
            ⟳
          </button>
          {lastUpdate && (
            <span className="last-update">
              Updated: {lastUpdate.toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      {/* Overall metric */}
      <OverallMetric stats={data.overall} />

      {/* Domains breakdown */}
      {Object.keys(data.domains).length > 0 && (
        <div className="section">
          <h3>By Domain</h3>
          <div className="cards-grid" data-testid="domains-grid">
            {Object.entries(data.domains).map(([domain, stats]) => (
              <StatCard key={domain} label={domain} stats={stats} />
            ))}
          </div>
        </div>
      )}

      {/* Lanes breakdown */}
      {Object.keys(data.lanes).length > 0 && (
        <div className="section">
          <h3>By Lane</h3>
          <div className="cards-grid" data-testid="lanes-grid">
            {Object.entries(data.lanes).map(([lane, stats]) => (
              <StatCard key={lane} label={lane} stats={stats} />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {Object.keys(data.domains).length === 0 && Object.keys(data.lanes).length === 0 && (
        <div className="empty-section">
          <p>No domain or lane data available yet</p>
        </div>
      )}
    </div>
  );
}

function OverallMetric({ stats }: { stats: FirstTryStats }) {
  const percent = Math.round(stats.rate * 100);
  const total = stats.first_try + stats.needed_repair;

  return (
    <div className="overall-metric" data-testid="overall-metric">
      <div className="metric-value">{percent}%</div>
      <div className="metric-details">
        <div className="detail">
          <span className="label">First Try:</span>
          <span className="count">{stats.first_try}</span>
        </div>
        <div className="detail">
          <span className="label">Needed Repair:</span>
          <span className="count">{stats.needed_repair}</span>
        </div>
        <div className="detail">
          <span className="label">Total:</span>
          <span className="count">{total}</span>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, stats }: { label: string; stats: FirstTryStats }) {
  const percent = Math.round(stats.rate * 100);
  const total = stats.first_try + stats.needed_repair;

  let healthClass = 'neutral';
  if (percent >= 90) healthClass = 'excellent';
  else if (percent >= 70) healthClass = 'good';
  else if (percent >= 50) healthClass = 'fair';
  else healthClass = 'poor';

  return (
    <div className={`stat-card health-${healthClass}`} data-testid={`stat-card-${label}`}>
      <div className="card-header">
        <h4>{label}</h4>
        <span className="rate-percent">{percent}%</span>
      </div>

      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="card-stats">
        <div className="stat">
          <span className="label">First Try:</span>
          <span className="value">{stats.first_try}</span>
        </div>
        <div className="stat">
          <span className="label">Repairs:</span>
          <span className="value">{stats.needed_repair}</span>
        </div>
        <div className="stat">
          <span className="label">Total:</span>
          <span className="value">{total}</span>
        </div>
      </div>
    </div>
  );
}
