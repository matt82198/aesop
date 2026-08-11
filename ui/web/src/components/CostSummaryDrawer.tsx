/**
 * CostSummaryDrawer — persistent collapsible drawer on right edge showing:
 * - Total spend (or tokens if no pricing)
 * - Spend rate (cost per day, or token burn rate)
 * - Model-mix breakdown (compact bar chart or list)
 *
 * Default: collapsed (toggle rail only, ~40px). Expanded: ~180px width.
 * Handles three data states: loading (live but no data yet), error (connection issue),
 * empty (no runs). Never renders a blank pane.
 *
 * Bound to same SSE source as Cost view; no polling.
 */

import { useState, useMemo } from 'react';
import type { CostSummary, SSEConnectionStatus } from '../lib/types';
import { TESTIDS } from '../test/fixtures';
import './CostSummaryDrawer.css';

interface CostSummaryDrawerProps {
  cost: CostSummary | null;
  connectionStatus: SSEConnectionStatus;
}

// Empty cost for initialization
const EMPTY_COST: CostSummary = {
  models: {},
  daily_totals: {},
  overall_scorecard: {
    total_runs: 0,
    ok_count: 0,
    failed_count: 0,
    empty_count: 0,
    hung_count: 0,
    ok_rate: 0,
    failed_rate: 0,
    empty_rate: 0,
    hung_rate: 0,
  },
  skipped_lines: 0,
  has_pricing: false,
  estimates_by_model: {},
  per_week_costs: {},
  per_wave_costs: {},
  per_agent_costs: {},
  verdict_weighted_cost: {
    cost_per_ok: 0,
    cost_per_failed: 0,
    cost_per_empty: 0,
    cost_per_hung: 0,
  },
  model_mix_trend: {},
};

export function CostSummaryDrawer({ cost, connectionStatus }: CostSummaryDrawerProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // State determination (same logic as Cost.tsx)
  const isLoading = !cost && connectionStatus.status === 'live';
  const isError = !cost && connectionStatus.status !== 'live';
  const isEmpty = cost && cost.overall_scorecard.total_runs === 0;

  const summary = cost ?? EMPTY_COST;

  // Compute total spend or tokens
  const totalSpend = useMemo(() => {
    if (!summary.has_pricing) {
      // Show total tokens
      const totalIn = Object.values(summary.models).reduce((sum, m) => sum + m.tokens_in, 0);
      const totalOut = Object.values(summary.models).reduce((sum, m) => sum + m.tokens_out, 0);
      return { formatted: formatTokens(totalIn + totalOut), isTokens: true };
    }

    // Show total cost
    const total = Object.values(summary.estimates_by_model).reduce((sum, est) => sum + est.total_cost, 0);
    return { formatted: `$${total.toFixed(2)}`, isTokens: false };
  }, [summary]);

  // Compute spend rate (daily average or token burn rate)
  const spendRate = useMemo(() => {
    if (isEmpty) return 'No data';

    const dailyKeys = Object.keys(summary.daily_totals);
    if (dailyKeys.length === 0) return 'No data';

    if (summary.has_pricing) {
      // Daily cost average
      const daysWithWeekData = Object.keys(summary.per_week_costs);
      if (daysWithWeekData.length === 0) return 'No data';

      const weekCosts = Object.values(summary.per_week_costs);
      const avgWeekly = weekCosts.reduce((sum, w) => sum + w.cost, 0) / weekCosts.length;
      const avgDaily = avgWeekly / 7;
      return `$${avgDaily.toFixed(2)}/day`;
    } else {
      // Token burn rate
      const dailyTotals = Object.values(summary.daily_totals);
      const totalTokens = dailyTotals.reduce((sum, d) => sum + d.tokens_in + d.tokens_out, 0);
      const avgDaily = totalTokens / dailyKeys.length;
      return `${formatTokens(Math.round(avgDaily))}/day`;
    }
  }, [summary, isEmpty]);

  // Compute model mix (percentage of total tokens by model in latest day)
  const modelMix = useMemo(() => {
    const sortedDays = Object.keys(summary.daily_totals).sort().reverse();
    if (sortedDays.length === 0) return [];

    const latestDay = sortedDays[0];
    const dayTokens = summary.daily_totals[latestDay];
    const dayTotal = dayTokens.tokens_in + dayTokens.tokens_out;

    if (dayTotal === 0) return [];

    // Calculate each model's share
    const modelShares: Array<{ model: string; percentage: number }> = [];
    Object.entries(summary.models).forEach(([model, stats]) => {
      const modelTotal = stats.tokens_in + stats.tokens_out;
      const percentage = (modelTotal / (dayTotal * Object.keys(summary.models).length)) * 100;
      if (percentage > 0) {
        modelShares.push({ model, percentage });
      }
    });

    return modelShares.sort((a, b) => b.percentage - a.percentage).slice(0, 3);
  }, [summary]);

  // Format model name for display
  const formatModelName = (model: string) => {
    const parts = model.split('-');
    if (parts[0] === 'claude') {
      return parts.slice(1, 3).join('-').toUpperCase();
    }
    return model.substring(0, 8);
  };

  return (
    <aside
      className={`cost-summary-drawer ${isExpanded ? 'cost-summary-drawer--expanded' : ''}`}
      data-testid={TESTIDS.costSummaryDrawer}
      aria-label="Cost summary drawer"
    >
      <button
        className="cost-summary-drawer__toggle"
        onClick={() => setIsExpanded(!isExpanded)}
        aria-label={isExpanded ? 'Close cost summary' : 'Open cost summary'}
        data-testid={TESTIDS.costSummaryDrawerToggle}
        type="button"
      >
        <span className="cost-summary-drawer__toggle-icon">$</span>
      </button>

      <div
        className="cost-summary-drawer__panel"
        role="status"
        aria-hidden={!isExpanded}
        data-testid={TESTIDS.costSummaryDrawerPanel}
      >
        {isLoading && (
          <div className="cost-summary__state" data-testid={TESTIDS.costSummaryLoading}>
            <p className="cost-summary__label">Loading...</p>
            <div className="cost-summary__spinner" />
          </div>
        )}

        {isError && (
          <div className="cost-summary__state cost-summary__state--error" data-testid={TESTIDS.costSummaryError}>
            <p className="cost-summary__label">Error</p>
            {connectionStatus.lastError && (
              <p className="cost-summary__message">{connectionStatus.lastError}</p>
            )}
          </div>
        )}

        {isEmpty && (
          <div className="cost-summary__state" data-testid={TESTIDS.costSummaryEmpty}>
            <p className="cost-summary__label">No data yet</p>
            <p className="cost-summary__hint">Data appears as agents run.</p>
          </div>
        )}

        {!isLoading && !isError && !isEmpty && (
          <>
            <div className="cost-summary__metric" data-testid={TESTIDS.costSummaryTotal}>
              <span className="cost-summary__metric-label">Total</span>
              <span className="cost-summary__metric-value">{totalSpend.formatted}</span>
            </div>

            <div className="cost-summary__metric" data-testid={TESTIDS.costSummaryRate}>
              <span className="cost-summary__metric-label">Rate</span>
              <span className="cost-summary__metric-value">{spendRate}</span>
            </div>

            {modelMix.length > 0 && (
              <div className="cost-summary__breakdown" data-testid={TESTIDS.costSummaryModelMix}>
                <span className="cost-summary__breakdown-label">Models</span>
                <div className="cost-summary__model-list">
                  {modelMix.map(({ model, percentage }) => (
                    <div key={model} className="cost-summary__model-row">
                      <span className="cost-summary__model-name">
                        {formatModelName(model)}
                      </span>
                      <span className="cost-summary__model-bar">
                        <span
                          className="cost-summary__model-fill"
                          style={{ width: `${Math.min(percentage * 2, 100)}%` }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

// Helper: format token count with K/M suffix
function formatTokens(count: number): string {
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1)}M`;
  }
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1)}K`;
  }
  return count.toString();
}
