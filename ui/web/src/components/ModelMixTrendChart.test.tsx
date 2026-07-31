/**
 * ModelMixTrendChart component tests
 * Tests the model mix trend visualization
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ModelMixTrendChart } from './ModelMixTrendChart';
import { fixtureCost, TESTIDS } from '../test/fixtures';

describe('ModelMixTrendChart', () => {
  it('renders empty state when no model mix data', () => {
    const emptyData = { ...fixtureCost, model_mix_trend: {} };
    render(<ModelMixTrendChart cost={emptyData} />);

    expect(screen.getByTestId(TESTIDS.modelMixChart)).toBeInTheDocument();
    expect(screen.getByText(/no model mix data yet/i)).toBeInTheDocument();
  });

  it('renders SVG chart when data available', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    const svg = screen.getByRole('img', { hidden: true });
    expect(svg).toBeInTheDocument();
    expect(svg.tagName).toBe('svg');
  });

  it('displays date labels on x-axis', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    // fixture has dates: 2026-07-11, 2026-07-12, 2026-07-13
    // x-axis labels show just the day (11, 12, 13)
    expect(screen.getByText('11')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('13')).toBeInTheDocument();
  });

  it('includes legend with model names', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    // Legend should show abbreviated model names (in SVG)
    const svg = screen.getByRole('img', { hidden: true });
    const svgText = svg.textContent || '';
    expect(svgText).toContain('haiku');
  });

  it('renders stacked bar segments for each model', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    // Should have rect elements for each model segment
    const rects = document.querySelectorAll('.bar-segment');
    expect(rects.length).toBeGreaterThan(0);
  });

  it('has tooltips on bar segments', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    const rects = document.querySelectorAll('.bar-segment');
    // Each rect should have a title for tooltip
    rects.forEach((rect) => {
      const title = rect.querySelector('title');
      expect(title).toBeTruthy();
    });
  });

  it('labels percentage on tooltips', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    const titles = Array.from(document.querySelectorAll('.bar-segment title'));
    const titleTexts = titles.map((t) => t.textContent);

    // Should have percentage in tooltip
    const hasPercentage = titleTexts.some((text) => text?.includes('%'));
    expect(hasPercentage).toBe(true);
  });

  it('shows informational note about chart meaning', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    expect(screen.getByText(/daily model distribution/i)).toBeInTheDocument();
  });

  it('displays Y-axis label', () => {
    render(<ModelMixTrendChart cost={fixtureCost} />);

    // Y-axis label should be %
    const yLabels = screen.getAllByText('%');
    expect(yLabels.length).toBeGreaterThan(0);
  });

  it('renders empty state when total_runs is 0', () => {
    const emptyRuns = {
      ...fixtureCost,
      overall_scorecard: { ...fixtureCost.overall_scorecard, total_runs: 0 },
    };
    render(<ModelMixTrendChart cost={emptyRuns} />);

    expect(screen.getByText(/no model mix data yet/i)).toBeInTheDocument();
  });

  describe('accessibility', () => {
    it('SVG has role="img" for semantic meaning', () => {
      render(<ModelMixTrendChart cost={fixtureCost} />);
      const svg = screen.getByRole('img', { hidden: true });
      expect(svg).toHaveAttribute('role', 'img');
    });

    it('SVG has aria-label describing the chart', () => {
      render(<ModelMixTrendChart cost={fixtureCost} />);
      const svg = screen.getByRole('img', { hidden: true });
      expect(svg).toHaveAttribute('aria-label');
      const label = svg?.getAttribute('aria-label');
      expect(label).toBeTruthy();
      expect(label).toMatch(/model|distribution|usage/i);
    });

    it('bar segments have <title> elements for tooltips', () => {
      render(<ModelMixTrendChart cost={fixtureCost} />);
      const titles = Array.from(document.querySelectorAll('.bar-segment title'));
      expect(titles.length).toBeGreaterThan(0);
      // Each title should have content describing the data
      titles.forEach((title) => {
        expect(title.textContent).toBeTruthy();
        expect(title.textContent).toMatch(/:/); // Should have date: model percentage format
      });
    });
  });
});
