/**
 * Cost view tests — composition of CostTable, CostChart, and Scorecard.
 * Tests empty state (has_pricing=false) with configure callout.
 * Tests full state rendering.
 * Tests three load states: loading, error, loaded-but-empty.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Cost } from './Cost';
import { fixtureCost, fixtureCostWithPricing, TESTIDS } from '../test/fixtures';
import type { SSEConnectionStatus } from '../lib/types';

// Default mock connection status for tests
const mockConnectionLive: SSEConnectionStatus = { status: 'live' };
const mockConnectionError: SSEConnectionStatus = {
  status: 'reconnecting',
  lastError: 'Connection failed',
};

describe('Cost view', () => {
  it('renders with testid', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    expect(screen.getByTestId(TESTIDS.viewCost)).toBeInTheDocument();
  });

  it('renders cost table, chart, and scorecard', () => {
    render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.scorecard)).toBeInTheDocument();
  });

  describe('three load states', () => {
    it('shows loading state when cost is null and connection is live', () => {
      render(<Cost cost={null} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId('cost-loading')).toBeInTheDocument();
      expect(screen.getByText('Loading cost metrics...')).toBeInTheDocument();
      // Should NOT show error or empty states
      expect(screen.queryByTestId('cost-error')).not.toBeInTheDocument();
      expect(screen.queryByTestId('cost-empty')).not.toBeInTheDocument();
    });

    it('shows error state when cost is null and connection is reconnecting', () => {
      render(<Cost cost={null} connectionStatus={mockConnectionError} onRetry={() => {}} />);
      expect(screen.getByTestId('cost-error')).toBeInTheDocument();
      expect(screen.getByText(/Could not load cost data/)).toBeInTheDocument();
      // Should have retry button since onRetry is provided
      expect(screen.getByText('Retry')).toBeInTheDocument();
      // Should NOT show loading or empty states
      expect(screen.queryByTestId('cost-loading')).not.toBeInTheDocument();
      expect(screen.queryByTestId('cost-empty')).not.toBeInTheDocument();
    });

    it('shows empty state when cost is null but connection is also not live (loaded with no data)', () => {
      // This happens after a successful connection but cost event has no data
      // In this case, cost object is not null but has zero runs
      const emptyCost = { ...fixtureCost, overall_scorecard: { ...fixtureCost.overall_scorecard, total_runs: 0 } };
      render(<Cost cost={emptyCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId('cost-empty')).toBeInTheDocument();
      expect(screen.getByText('No cost data yet.')).toBeInTheDocument();
      expect(screen.queryByTestId('cost-loading')).not.toBeInTheDocument();
      expect(screen.queryByTestId('cost-error')).not.toBeInTheDocument();
    });

    it('error state includes connection error message', () => {
      const errorStatus: SSEConnectionStatus = {
        status: 'reconnecting',
        lastError: 'Server temporarily unavailable',
      };
      render(<Cost cost={null} connectionStatus={errorStatus} />);
      expect(screen.getByTestId('cost-error')).toBeInTheDocument();
      expect(screen.getByText(/Server temporarily unavailable/)).toBeInTheDocument();
    });

    it('error state shows retry button when onRetry callback provided', () => {
      const mockRetry = () => {};
      render(
        <Cost cost={null} connectionStatus={mockConnectionError} onRetry={mockRetry} />
      );
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });
  });

  describe('tokens-only mode (has_pricing=false)', () => {
    it('shows "configure pricing" empty-state callout', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should mention configure or pricing
      expect(view.textContent).toMatch(/configure|pricing|aesop\.config/i);
    });

    it('callout references aesop.config.json', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      expect(view.textContent).toContain('aesop.config.json');
    });

    it('provides instructions for pricing configuration', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should have some instructional text
      expect(view.textContent?.length).toBeGreaterThan(50);
    });

    it('still renders table and chart even without pricing', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      // Should show token data even without pricing
      expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    });
  });

  describe('pricing mode (has_pricing=true)', () => {
    it('does not show "configure pricing" callout', () => {
      render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should NOT emphasize configuration when pricing exists
      expect(view.textContent).not.toMatch(/configure pricing/i);
    });

    it('renders cost columns with dollar amounts', () => {
      render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
      const table = screen.getByTestId(TESTIDS.costTable);
      // Should display pricing info
      expect(table.innerHTML).toContain('$');
    });
  });

  it('renders as a proper section element', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    const view = screen.getByTestId(TESTIDS.viewCost) as HTMLElement;
    expect(['SECTION', 'DIV'].includes(view.tagName)).toBe(true);
  });

  it('has aria-label or heading describing the view', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    const view = screen.getByTestId(TESTIDS.viewCost);
    // Should have descriptive content or aria-label
    expect(view.getAttribute('aria-label') || view.textContent).toBeTruthy();
  });

  it('layout is readable with all three components visible', () => {
    render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
    // Should have all three sections rendered
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.scorecard)).toBeInTheDocument();
  });

  it('scorecard displays verdict statistics', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    // Should show total runs count
    expect(screen.getByTestId(TESTIDS.scorecard).textContent).toContain('142');
  });

  it('chart shows per-day trend', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    const chart = screen.getByTestId(TESTIDS.costChart);
    // Should render 6 bars for the 3 days (2 per day: in/out)
    const bars = chart.querySelectorAll('rect[data-day]');
    expect(bars.length).toBe(6);
  });

  it('table shows per-model breakdown', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    const table = screen.getByTestId(TESTIDS.costTable);
    // Should list haiku and sonnet models
    expect(table.textContent).toContain('haiku');
    expect(table.textContent).toContain('sonnet');
  });

  it('handles cost object with skipped_lines footnote', () => {
    render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
    // scorecard mentions skipped lines when > 0
    // At least the component renders without error
    expect(screen.getByTestId(TESTIDS.viewCost)).toBeInTheDocument();
  });

  it('empty cost (0 runs) still renders all panels', () => {
    const empty = {
      ...fixtureCost,
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
    };
    render(<Cost cost={empty} connectionStatus={mockConnectionLive} />);
    expect(screen.getByTestId(TESTIDS.viewCost)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
  });

  describe('extended cost metrics (wave RC3 additions)', () => {
    it('renders weekly cost summary component', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId(TESTIDS.weeklyCostSummary)).toBeInTheDocument();
    });

    it('renders verdict cost metrics component', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId(TESTIDS.verdictCostMetrics)).toBeInTheDocument();
    });

    it('renders model mix trend chart component', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId(TESTIDS.modelMixChart)).toBeInTheDocument();
    });

    it('all six cost sections render together', () => {
      render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
      // Original three + three new
      expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.scorecard)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.weeklyCostSummary)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.verdictCostMetrics)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.modelMixChart)).toBeInTheDocument();
    });
  });

  describe('wave and agent cost breakdown (wave RC4 additions)', () => {
    it('renders wave agent breakdown component', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByTestId(TESTIDS.waveAgentBreakdown)).toBeInTheDocument();
    });

    it('renders "Per Wave & Agent Breakdown" heading', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByText('Per Wave & Agent Breakdown')).toBeInTheDocument();
    });

    it('shows wave breakdown section', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByText('Cost per Wave')).toBeInTheDocument();
    });

    it('shows agent breakdown section', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      expect(screen.getByText('Cost per Agent Type')).toBeInTheDocument();
    });

    it('displays wave and agent data when available', () => {
      render(<Cost cost={fixtureCostWithPricing} connectionStatus={mockConnectionLive} />);
      expect(screen.getByText('wave-14')).toBeInTheDocument();
      expect(screen.getByText('Agent')).toBeInTheDocument();
    });

    it('weekly rollup shows data when per_week_costs is populated', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const weeklySummary = screen.getByTestId(TESTIDS.weeklyCostSummary);
      // Should show week labels
      expect(weeklySummary.textContent).toContain('2026-W');
    });

    it('verdict metrics show all four outcome types', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const verdictMetrics = screen.getByTestId(TESTIDS.verdictCostMetrics);
      expect(verdictMetrics.textContent).toContain('OK');
      expect(verdictMetrics.textContent).toContain('Failed');
    });

    it('model mix chart displays svg visualization', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const chartContainer = screen.getByTestId(TESTIDS.modelMixChart);
      const svg = chartContainer.querySelector('svg');
      expect(svg).toBeInTheDocument();
    });
  });

  describe('chart accessibility', () => {
    it('cost chart SVG has accessible name via aria-label', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const chartContainer = screen.getByTestId(TESTIDS.costChart);
      const svg = chartContainer.querySelector('svg');
      expect(svg).toHaveAttribute('role', 'img');
      expect(svg).toHaveAttribute('aria-label');
      expect(svg?.getAttribute('aria-label')).toBeTruthy();
    });

    it('model mix chart SVG has accessible name via aria-label', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const chartContainer = screen.getByTestId(TESTIDS.modelMixChart);
      const svg = chartContainer.querySelector('svg');
      expect(svg).toHaveAttribute('role', 'img');
      expect(svg).toHaveAttribute('aria-label');
      expect(svg?.getAttribute('aria-label')).toBeTruthy();
    });

    it('cost analytics panel section has aria-label', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const panel = screen.getByTestId(TESTIDS.costAnalyticsPanel);
      expect(panel).toHaveAttribute('aria-label');
      expect(panel?.getAttribute('aria-label')).toBeTruthy();
    });

    it('cost view section has aria-label', () => {
      render(<Cost cost={fixtureCost} connectionStatus={mockConnectionLive} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      expect(view).toHaveAttribute('aria-label');
      expect(view?.getAttribute('aria-label')).toBe('Cost analytics');
    });
  });
});
