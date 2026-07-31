/**
 * CostChart component tests — pure SVG bar chart for per-day tokens.
 * Tests empty data, single day, many days, axis labels, titles.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CostChart } from './CostChart';
import { fixtureCost, fixtureCostWithPricing, TESTIDS } from '../test/fixtures';

describe('CostChart', () => {
  it('renders chart with testid', () => {
    render(<CostChart cost={fixtureCost} />);
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
  });

  it('renders as an SVG element (inside container)', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
  });

  it('renders bars for each day in daily_totals', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    // fixture has 3 days: 2026-07-11, 2026-07-12, 2026-07-13
    // Each day has 2 bars (input and output), so 6 rects total
    const rects = svg.querySelectorAll('rect[data-day]');
    expect(rects.length).toBe(6);
  });

  it('sets data-day attribute on each bar for testability', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    expect(svg.querySelector('rect[data-day="2026-07-11"]')).toBeInTheDocument();
    expect(svg.querySelector('rect[data-day="2026-07-12"]')).toBeInTheDocument();
    expect(svg.querySelector('rect[data-day="2026-07-13"]')).toBeInTheDocument();
  });

  it('includes <title> elements for accessibility on each bar', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    const titles = svg.querySelectorAll('title');
    expect(titles.length).toBeGreaterThan(0);
  });

  it('scales bars proportionally to token counts', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    const day11 = svg.querySelector('rect[data-day="2026-07-11"]') as SVGElement;
    const day12 = svg.querySelector('rect[data-day="2026-07-12"]') as SVGElement;
    // day11: 1204000 tokens, day12: 986170 tokens
    // day11 should be taller than day12
    const height11 = parseFloat(day11?.getAttribute('height') || '0');
    const height12 = parseFloat(day12?.getAttribute('height') || '0');
    expect(height11).toBeGreaterThan(height12);
  });

  it('handles single day without breaking layout', () => {
    const single = {
      ...fixtureCost,
      daily_totals: {
        '2026-07-13': fixtureCost.daily_totals['2026-07-13'],
      },
    };
    render(<CostChart cost={single} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    const rects = svg.querySelectorAll('rect[data-day]');
    // Single day has 2 bars (input and output)
    expect(rects.length).toBe(2);
  });

  it('handles empty daily_totals gracefully', () => {
    const empty = {
      ...fixtureCost,
      daily_totals: {},
    };
    render(<CostChart cost={empty} />);
    const chart = screen.getByTestId(TESTIDS.costChart);
    // Should still render (possibly with placeholder or empty state)
    expect(chart).toBeInTheDocument();
  });

  it('provides axis labels for readability', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    // Should have text elements for dates or axis labels
    const texts = svg.querySelectorAll('text');
    expect(texts.length).toBeGreaterThan(0);
  });

  it('uses theme color tokens (no hex colors)', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    // Should use CSS vars, not inline hex
    const style = svg.getAttribute('style') || '';
    expect(style).not.toMatch(/#[0-9a-fA-F]{3,6}(?![0-9a-fA-F])/);
  });

  it('works with pricing fixture', () => {
    render(<CostChart cost={fixtureCostWithPricing} />);
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
  });

  it('does not overflow parent container', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg');
    const viewBox = svg?.getAttribute('viewBox');
    expect(viewBox).toBeTruthy();
    // SVG should use viewBox for responsive scaling
  });

  it('clamping: does not break with extreme token counts', () => {
    const extreme = {
      ...fixtureCost,
      daily_totals: {
        '2026-07-13': {
          tokens_in: 999999999,
          tokens_out: 999999999,
        },
      },
    };
    render(<CostChart cost={extreme} />);
    expect(screen.getByTestId(TESTIDS.costChart)).toBeInTheDocument();
  });

  it('chart has proper SVG structure (g elements for groups)', () => {
    render(<CostChart cost={fixtureCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;
    // Should use <g> for grouping logical elements
    const groups = svg.querySelectorAll('g');
    expect(groups.length).toBeGreaterThan(0);
  });

  it('bars are stacked (output offset by input height, not grouped side-by-side)', () => {
    const stackedCost = {
      ...fixtureCost,
      daily_totals: {
        '2026-07-15': {
          tokens_in: 1000,
          tokens_out: 2000,
        },
      },
    };
    render(<CostChart cost={stackedCost} />);
    const container = screen.getByTestId(TESTIDS.costChart);
    const svg = container.querySelector('svg') as SVGElement;

    // Should have exactly 2 bars for one day (input + output stacked)
    const rects = svg.querySelectorAll('rect[data-day="2026-07-15"]');
    expect(rects.length).toBe(2);

    const inputBar = rects[0] as SVGRectElement;
    const outputBar = rects[1] as SVGRectElement;

    const inputX = parseFloat(inputBar.getAttribute('x') || '0');
    const outputX = parseFloat(outputBar.getAttribute('x') || '0');
    const inputY = parseFloat(inputBar.getAttribute('y') || '0');
    const outputY = parseFloat(outputBar.getAttribute('y') || '0');

    // For stacked bars: both must have the same x position (same column)
    expect(inputX).toEqual(outputX);

    // Output bar Y should be above input bar Y (smaller Y in SVG coords)
    // Output Y should equal: input Y - outputHeight (since taller output is higher up)
    // OR: outputY + outputHeight = inputY (output bar ends where input bar starts)
    const outputHeight = parseFloat(outputBar.getAttribute('height') || '0');
    const expectedOutputY = inputY - outputHeight;
    expect(outputY).toBeCloseTo(expectedOutputY, 1);
  });

  describe('accessibility', () => {
    it('SVG has role="img" for semantic meaning', () => {
      render(<CostChart cost={fixtureCost} />);
      const container = screen.getByTestId(TESTIDS.costChart);
      const svg = container.querySelector('svg');
      expect(svg).toHaveAttribute('role', 'img');
    });

    it('SVG has aria-label describing the chart', () => {
      render(<CostChart cost={fixtureCost} />);
      const container = screen.getByTestId(TESTIDS.costChart);
      const svg = container.querySelector('svg');
      expect(svg).toHaveAttribute('aria-label');
      const label = svg?.getAttribute('aria-label');
      expect(label).toBeTruthy();
      expect(label).toMatch(/token|daily|trend/i);
    });

    it('bar segments have <title> elements for tooltips', () => {
      render(<CostChart cost={fixtureCost} />);
      const container = screen.getByTestId(TESTIDS.costChart);
      const svg = container.querySelector('svg') as SVGElement;
      const titles = svg.querySelectorAll('rect[data-day] > title');
      expect(titles.length).toBeGreaterThan(0);
      // Each bar should have a title
      const bars = svg.querySelectorAll('rect[data-day]');
      expect(titles.length).toBe(bars.length);
    });
  });
});
