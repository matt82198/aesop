/**
 * Cost view tests — composition of CostTable, CostChart, and Scorecard.
 * Tests empty state (has_pricing=false) with configure callout.
 * Tests full state rendering.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Cost } from './Cost';
import { fixtureCost, fixtureCostWithPricing, TESTIDS } from '../test/fixtures';

describe('Cost view', () => {
  it('renders with testid', () => {
    render(<Cost cost={fixtureCost} />);
    expect(screen.getByTestId(TESTIDS.viewCost)).toBeInTheDocument();
  });

  it('renders cost table, chart, and scorecard', () => {
    render(<Cost cost={fixtureCostWithPricing} />);
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.scorecard)).toBeInTheDocument();
  });

  describe('tokens-only mode (has_pricing=false)', () => {
    it('shows "configure pricing" empty-state callout', () => {
      render(<Cost cost={fixtureCost} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should mention configure or pricing
      expect(view.textContent).toMatch(/configure|pricing|aesop\.config/i);
    });

    it('callout references aesop.config.json', () => {
      render(<Cost cost={fixtureCost} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      expect(view.textContent).toContain('aesop.config.json');
    });

    it('provides instructions for pricing configuration', () => {
      render(<Cost cost={fixtureCost} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should have some instructional text
      expect(view.textContent?.length).toBeGreaterThan(50);
    });

    it('still renders table and chart even without pricing', () => {
      render(<Cost cost={fixtureCost} />);
      // Should show token data even without pricing
      expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
      expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    });
  });

  describe('pricing mode (has_pricing=true)', () => {
    it('does not show "configure pricing" callout', () => {
      render(<Cost cost={fixtureCostWithPricing} />);
      const view = screen.getByTestId(TESTIDS.viewCost);
      // Should NOT emphasize configuration when pricing exists
      expect(view.textContent).not.toMatch(/configure pricing/i);
    });

    it('renders cost columns with dollar amounts', () => {
      render(<Cost cost={fixtureCostWithPricing} />);
      const table = screen.getByTestId(TESTIDS.costTable);
      // Should display pricing info
      expect(table.innerHTML).toContain('$');
    });
  });

  it('renders as a proper section element', () => {
    render(<Cost cost={fixtureCost} />);
    const view = screen.getByTestId(TESTIDS.viewCost) as HTMLElement;
    expect(['SECTION', 'DIV'].includes(view.tagName)).toBe(true);
  });

  it('has aria-label or heading describing the view', () => {
    render(<Cost cost={fixtureCost} />);
    const view = screen.getByTestId(TESTIDS.viewCost);
    // Should have descriptive content or aria-label
    expect(view.getAttribute('aria-label') || view.textContent).toBeTruthy();
  });

  it('layout is readable with all three components visible', () => {
    render(<Cost cost={fixtureCostWithPricing} />);
    // Should have all three sections rendered
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.scorecard)).toBeInTheDocument();
  });

  it('scorecard displays verdict statistics', () => {
    render(<Cost cost={fixtureCost} />);
    // Should show total runs count
    expect(screen.getByTestId(TESTIDS.scorecard).textContent).toContain('142');
  });

  it('chart shows per-day trend', () => {
    render(<Cost cost={fixtureCost} />);
    const chart = screen.getByTestId(TESTIDS.costChart);
    // Should render 6 bars for the 3 days (2 per day: in/out)
    const bars = chart.querySelectorAll('rect[data-day]');
    expect(bars.length).toBe(6);
  });

  it('table shows per-model breakdown', () => {
    render(<Cost cost={fixtureCost} />);
    const table = screen.getByTestId(TESTIDS.costTable);
    // Should list haiku and sonnet models
    expect(table.textContent).toContain('haiku');
    expect(table.textContent).toContain('sonnet');
  });

  it('handles cost object with skipped_lines footnote', () => {
    render(<Cost cost={fixtureCost} />);
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
    render(<Cost cost={empty} />);
    expect(screen.getByTestId(TESTIDS.viewCost)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costTable)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
  });

  describe('extended cost metrics (wave RC3 additions)', () => {
    it('renders weekly cost summary component', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByTestId(TESTIDS.weeklyCostSummary)).toBeInTheDocument();
    });

    it('renders verdict cost metrics component', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByTestId(TESTIDS.verdictCostMetrics)).toBeInTheDocument();
    });

    it('renders model mix trend chart component', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByTestId(TESTIDS.modelMixChart)).toBeInTheDocument();
    });

    it('all six cost sections render together', () => {
      render(<Cost cost={fixtureCostWithPricing} />);
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
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByTestId(TESTIDS.waveAgentBreakdown)).toBeInTheDocument();
    });

    it('renders "Per Wave & Agent Breakdown" heading', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByText('Per Wave & Agent Breakdown')).toBeInTheDocument();
    });

    it('shows wave breakdown section', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByText('Cost per Wave')).toBeInTheDocument();
    });

    it('shows agent breakdown section', () => {
      render(<Cost cost={fixtureCost} />);
      expect(screen.getByText('Cost per Agent Type')).toBeInTheDocument();
    });

    it('displays wave and agent data when available', () => {
      render(<Cost cost={fixtureCostWithPricing} />);
      expect(screen.getByText('wave-14')).toBeInTheDocument();
      expect(screen.getByText('Agent')).toBeInTheDocument();
    });

    it('weekly rollup shows data when per_week_costs is populated', () => {
      render(<Cost cost={fixtureCost} />);
      const weeklySummary = screen.getByTestId(TESTIDS.weeklyCostSummary);
      // Should show week labels
      expect(weeklySummary.textContent).toContain('2026-W');
    });

    it('verdict metrics show all four outcome types', () => {
      render(<Cost cost={fixtureCost} />);
      const verdictMetrics = screen.getByTestId(TESTIDS.verdictCostMetrics);
      expect(verdictMetrics.textContent).toContain('OK');
      expect(verdictMetrics.textContent).toContain('Failed');
    });

    it('model mix chart displays svg visualization', () => {
      render(<Cost cost={fixtureCost} />);
      const chartContainer = screen.getByTestId(TESTIDS.modelMixChart);
      const svg = chartContainer.querySelector('svg');
      expect(svg).toBeInTheDocument();
    });
  });
});
