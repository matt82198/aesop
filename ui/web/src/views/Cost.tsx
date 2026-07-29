/**
 * Cost view — composition of cost table, chart, scorecard, and extended metrics.
 * Shows per-model breakdowns, per-day trends, verdict quality metrics,
 * weekly rollups, cost-per-outcome weighting, and model-mix trends.
 * When has_pricing=false, shows a "configure pricing" empty-state callout.
 * When has_pricing=true, displays dollar estimates alongside tokens.
 *
 * Error handling: displays a graceful error state if cost data is not available
 * or if the SSE stream fails to deliver cost metrics.
 */

import type { CostSummary } from '../lib/types';
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
  onRetry?: () => void;
}

export function Cost({ cost, onRetry }: CostProps) {
  if (!cost) {
    return (
      <section className="view-cost" data-testid={TESTIDS.viewCost} aria-label="Cost analytics">
        <h2>Cost Analytics</h2>
        <div className="cost-error" role="alert" data-testid="cost-error">
          <h3>Could not load cost data</h3>
          <p>The cost metrics failed to load. This may be a temporary issue with the backend.</p>
          {onRetry && (
            <button type="button" className="cost-error__retry" onClick={onRetry}>
              Retry
            </button>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className="view-cost" data-testid={TESTIDS.viewCost} aria-label="Cost analytics">
      <h2>Cost Analytics</h2>

      {!cost.has_pricing && (
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
          <CostAnalyticsPanel cost={cost} ceilingTokens={1_000_000_000} />
        </div>

        <div className="cost-section cost-section--full">
          <h3>Per Wave & Agent Breakdown</h3>
          <WaveAgentBreakdown cost={cost} />
        </div>

        <div className="cost-section">
          <h3>By Model</h3>
          <CostTable cost={cost} />
        </div>

        <div className="cost-section">
          <h3>Daily Trend</h3>
          <CostChart cost={cost} />
        </div>

        <div className="cost-section">
          <h3>Quality Scorecard</h3>
          <Scorecard cost={cost} />
        </div>

        <div className="cost-section">
          <h3>Weekly Rollup</h3>
          <WeeklyCostSummary cost={cost} />
        </div>

        <div className="cost-section">
          <h3>Cost per Outcome</h3>
          <VerdictCostMetrics cost={cost} />
        </div>

        <div className="cost-section">
          <h3>Model Mix Trend</h3>
          <ModelMixTrendChart cost={cost} />
        </div>
      </div>
    </section>
  );
}
