/**
 * Cost view â€” composition of cost table, chart, scorecard, and extended metrics.
 * Shows per-model breakdowns, per-day trends, verdict quality metrics,
 * weekly rollups, cost-per-outcome weighting, and model-mix trends.
 * When has_pricing=false, shows a "configure pricing" empty-state callout.
 * When has_pricing=true, displays dollar estimates alongside tokens.
 *
 * Error handling: displays a graceful error state if cost data is not available
 * or if the SSE stream fails to deliver cost metrics.
 */

import type { CostSummary, SSEConnectionStatus } from '../lib/types';
import { CostTable } from '../components/CostTable';
import { CostChart } from '../components/CostChart';
import { Scorecard } from '../components/Scorecard';
import { WeeklyCostSummary } from '../components/WeeklyCostSummary';
import { VerdictCostMetrics } from '../components/VerdictCostMetrics';
import { ModelMixTrendChart } from '../components/ModelMixTrendChart';
import { CostAnalyticsPanel } from '../components/CostAnalyticsPanel';
import { WaveAgentBreakdown } from '../components/WaveAgentBreakdown';
import { TESTIDS } from '../test/fixtures';
import './Cost.css';

interface CostProps {
  cost: CostSummary | null;
  connectionStatus: SSEConnectionStatus;
  onRetry?: () => void;
}

// A cost summary with no rows. Used when the SSE snapshot has not delivered cost
// yet, so the panel still mounts instead of the view claiming a failure.
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

export function Cost({ cost, connectionStatus, onRetry }: CostProps) {
  // Distinguish three states:
  // 1. Loading: cost is null AND connection is live (still waiting for first event)
  // 2. Error: cost is null AND connection status indicates a problem (reconnecting after error or explicit error)
  // 3. Loaded but empty: cost is not null (data arrived) but has no runs

  const isLoading = !cost && connectionStatus.status === 'live';
  const isError = !cost && connectionStatus.status !== 'live';
  const summary: CostSummary = cost ?? EMPTY_COST;

  return (
    <section className="view-cost" data-testid={TESTIDS.viewCost} aria-label="Cost analytics">
      <h2>Cost Analytics</h2>

      {isLoading && (
        <div className="cost-callout cost-callout--info" role="status" data-testid="cost-loading">
          <p>Loading cost metrics...</p>
          <p className="cost-callout__hint">
            The first cost snapshot is being fetched from the server.
          </p>
        </div>
      )}

      {isError && (
        <div className="cost-callout cost-callout--error" role="alert" data-testid="cost-error">
          <p>
            <strong>Could not load cost data.</strong> {connectionStatus.lastError || 'Connection error.'}
          </p>
          {onRetry && (
            <button type="button" className="cost-error__retry" onClick={onRetry}>
              Retry
            </button>
          )}
        </div>
      )}

      {!isLoading && !isError && cost && cost.overall_scorecard.total_runs === 0 && (
        <div className="cost-callout cost-callout--info" role="status" data-testid="cost-empty">
          <p>No cost data yet.</p>
          <p className="cost-callout__hint">
            This appears on a fresh install with no outcomes ledger. Cost data will appear as agents
            complete runs.
          </p>
        </div>
      )}

      {cost && !summary.has_pricing && (
        <div className="cost-callout cost-callout--info" role="status">
          <h3>Configure Pricing</h3>
          <p>
            To see cost estimates, add a <code>pricing</code> map to your{' '}
            <code>aesop.config.json</code> with per-model input and output rates (e.g.{' '}
            <code>{'{input: 0.003, output: 0.015}'}</code>). Without pricing, token counts are
            shown; no estimates are computed.
          </p>
        </div>
      )}

      <div className="cost-layout">
        <div className="cost-section cost-section--full">
          <CostAnalyticsPanel cost={summary} ceilingTokens={1_000_000_000} />
        </div>

        <div className="cost-section cost-section--full">
          <h3>Per Wave & Agent Breakdown</h3>
          <WaveAgentBreakdown cost={summary} />
        </div>

        <div className="cost-section">
          <h3>By Model</h3>
          <CostTable cost={summary} />
        </div>

        <div className="cost-section">
          <h3>Daily Trend</h3>
          <CostChart cost={summary} />
        </div>

        <div className="cost-section">
          <h3>Quality Scorecard</h3>
          <Scorecard cost={summary} />
        </div>

        <div className="cost-section">
          <h3>Weekly Rollup</h3>
          <WeeklyCostSummary cost={summary} />
        </div>

        <div className="cost-section">
          <h3>Cost per Outcome</h3>
          <VerdictCostMetrics cost={summary} />
        </div>

        <div className="cost-section">
          <h3>Model Mix Trend</h3>
          <ModelMixTrendChart cost={summary} />
        </div>
      </div>
    </section>
  );
}

