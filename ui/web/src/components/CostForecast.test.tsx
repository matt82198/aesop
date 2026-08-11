/**
 * Tests for CostForecast component.
 * Covers: trend calculation, chart rendering, three-state contract, accessibility.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CostForecast } from './CostForecast';
import type { CostSummary } from '../lib/types';

// Empty cost summary (no daily totals)
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

// Populated cost summary with 5 days of data (simple increasing trend)
const POPULATED_COST: CostSummary = {
  ...EMPTY_COST,
  daily_totals: {
    '2026-07-24': { tokens_in: 100000, tokens_out: 50000 },
    '2026-07-25': { tokens_in: 110000, tokens_out: 55000 },
    '2026-07-26': { tokens_in: 120000, tokens_out: 60000 },
    '2026-07-27': { tokens_in: 130000, tokens_out: 65000 },
    '2026-07-28': { tokens_in: 140000, tokens_out: 70000 },
  },
};

// Populated cost summary with only 1 day (insufficient for trend)
const SINGLE_DAY_COST: CostSummary = {
  ...EMPTY_COST,
  daily_totals: {
    '2026-07-28': { tokens_in: 140000, tokens_out: 70000 },
  },
};

describe('CostForecast', () => {
  describe('empty state (insufficient data)', () => {
    it('should render when daily_totals is empty', () => {
      render(<CostForecast cost={EMPTY_COST} />);
      const container = screen.getByTestId('cost-forecast-empty');
      expect(container).toBeInTheDocument();
      expect(screen.getByText(/Need at least 2 days/i)).toBeInTheDocument();
    });

    it('should render when only 1 day of data', () => {
      render(<CostForecast cost={SINGLE_DAY_COST} />);
      const container = screen.getByTestId('cost-forecast-empty');
      expect(container).toBeInTheDocument();
    });

    it('should display the title', () => {
      render(<CostForecast cost={EMPTY_COST} />);
      expect(screen.getByText('Cost Trend Forecast')).toBeInTheDocument();
    });
  });

  describe('populated state (with data)', () => {
    it('should render populated state when enough data', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      const container = screen.getByTestId('cost-forecast-populated');
      expect(container).toBeInTheDocument();
    });

    it('should render chart with aria-label for accessibility', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      const chart = screen.getByRole('img', { name: /Cost forecast/ });
      expect(chart).toBeInTheDocument();
      const ariaLabel = chart.getAttribute('aria-label');
      expect(ariaLabel).toMatch(/projected.*tokens at end of wave/i);
    });

    it('should render metrics panel with testid', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      const metrics = screen.getByTestId('cost-forecast-metrics');
      expect(metrics).toBeInTheDocument();
    });

    it('should display trend metrics (daily burn, projection, confidence)', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByText(/Trend \(daily burn\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Projected end-of-wave/i)).toBeInTheDocument();
      expect(screen.getByText(/90% confidence/i)).toBeInTheDocument();
    });

    it('should display daily burn rate (average tokens per day)', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      // POPULATED_COST has 5 days: [150k, 165k, 180k, 195k, 210k] = 900k total
      // average = 180k tokens/day
      expect(screen.getByText(/Trend \(daily burn\)/i)).toBeInTheDocument();
      // The value should be around 180,000
      const burnText = screen.getByText(/Trend \(daily burn\)/).closest('.metric-row');
      expect(burnText?.textContent).toMatch(/180,000|1[78][0-9],000/);
    });

    it('should show note about trend calculation', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByText(/linear regression/i)).toBeInTheDocument();
      expect(screen.getByText(/5 days/i)).toBeInTheDocument();
    });
  });

  describe('ceiling handling', () => {
    it('should render ceiling metrics when ceilingTokens provided', () => {
      const ceiling = 3_000_000_000; // 3B tokens
      render(<CostForecast cost={POPULATED_COST} ceilingTokens={ceiling} />);
      expect(screen.getByText(/Ceiling/)).toBeInTheDocument();
      expect(screen.getByText(/Projected vs ceiling/i)).toBeInTheDocument();
    });

    it('should show "Within" when projection is below ceiling', () => {
      const ceiling = 5_000_000_000; // 5B tokens (well above 28d projection)
      render(<CostForecast cost={POPULATED_COST} ceilingTokens={ceiling} />);
      const vsRow = screen.getByText(/Projected vs ceiling/i).closest('.metric-row');
      expect(vsRow?.textContent).toMatch(/✓.*Within/);
    });

    it('should show "Exceeds" alert when projection exceeds ceiling', () => {
      const ceiling = 200_000; // 200k tokens (well below projection of ~5M over 28 days)
      render(<CostForecast cost={POPULATED_COST} ceilingTokens={ceiling} />);
      const vsRow = screen.getByText(/Projected vs ceiling/i).closest('.metric-row');
      expect(vsRow?.textContent).toMatch(/⚠.*Exceeds/);
      expect(vsRow?.classList.contains('alert')).toBe(true);
    });

    it('should not show ceiling metrics when ceilingTokens is undefined', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.queryByText(/Ceiling:/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Projected vs ceiling/i)).not.toBeInTheDocument();
    });

    it('should show "no ceiling configured" callout when no ceiling', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByText(/No ceiling configured/i)).toBeInTheDocument();
    });
  });

  describe('linear regression accuracy', () => {
    it('should handle perfectly linear data', () => {
      const linearCost: CostSummary = {
        ...EMPTY_COST,
        daily_totals: {
          '2026-07-24': { tokens_in: 100000, tokens_out: 0 },
          '2026-07-25': { tokens_in: 120000, tokens_out: 0 },
          '2026-07-26': { tokens_in: 140000, tokens_out: 0 },
        },
      };
      render(<CostForecast cost={linearCost} />);
      expect(screen.getByTestId('cost-forecast-populated')).toBeInTheDocument();
      // Should project linearly: 20k tokens per day
    });

    it('should handle variable daily data', () => {
      const variableCost: CostSummary = {
        ...EMPTY_COST,
        daily_totals: {
          '2026-07-24': { tokens_in: 100000, tokens_out: 50000 },
          '2026-07-25': { tokens_in: 150000, tokens_out: 75000 },
          '2026-07-26': { tokens_in: 110000, tokens_out: 55000 },
          '2026-07-27': { tokens_in: 160000, tokens_out: 80000 },
          '2026-07-28': { tokens_in: 120000, tokens_out: 60000 },
        },
      };
      render(<CostForecast cost={variableCost} />);
      expect(screen.getByTestId('cost-forecast-populated')).toBeInTheDocument();
      // Should calculate trend with confidence bands
      expect(screen.getByText(/90% confidence/i)).toBeInTheDocument();
    });
  });

  describe('SVG accessibility', () => {
    it('chart should have role="img" and aria-label', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      const chart = screen.getByRole('img');
      expect(chart).toHaveAttribute('aria-label');
      expect(chart.getAttribute('aria-label')).toMatch(/Cost forecast/);
    });

    it('should not expose individual SVG paths as separate images', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      // Only the svg itself should have role="img", not the paths inside
      const imgs = screen.getAllByRole('img');
      expect(imgs).toHaveLength(1); // Just the main chart
    });
  });

  describe('three-state contract', () => {
    it('should never crash on empty input', () => {
      const badCost: CostSummary = {
        ...EMPTY_COST,
        daily_totals: undefined as any,
      };
      expect(() => render(<CostForecast cost={badCost} />)).not.toThrow();
    });

    it('state 1: empty (no data) should degrade gracefully', () => {
      render(<CostForecast cost={EMPTY_COST} />);
      expect(screen.getByTestId('cost-forecast-empty')).toBeInTheDocument();
      expect(screen.queryByTestId('cost-forecast-metrics')).not.toBeInTheDocument();
    });

    it('state 2: populated without ceiling should render metrics and callout', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByTestId('cost-forecast-populated')).toBeInTheDocument();
      expect(screen.getByTestId('cost-forecast-metrics')).toBeInTheDocument();
      expect(screen.getByText(/No ceiling configured/i)).toBeInTheDocument();
    });

    it('state 3: populated with ceiling should render all metrics', () => {
      render(<CostForecast cost={POPULATED_COST} ceilingTokens={5_000_000_000} />);
      expect(screen.getByTestId('cost-forecast-populated')).toBeInTheDocument();
      expect(screen.getByTestId('cost-forecast-metrics')).toBeInTheDocument();
      expect(screen.getByText(/Ceiling/)).toBeInTheDocument();
      expect(screen.queryByText(/No ceiling configured/i)).not.toBeInTheDocument();
    });
  });

  describe('data labeling honesty', () => {
    it('should label confidence band as "statistical estimate"', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByText(/labeled honestly as statistical estimate/i)).toBeInTheDocument();
    });

    it('should show day count in trend note', () => {
      render(<CostForecast cost={POPULATED_COST} />);
      expect(screen.getByText(/Trend: linear regression over 5 days/i)).toBeInTheDocument();
    });
  });
});
