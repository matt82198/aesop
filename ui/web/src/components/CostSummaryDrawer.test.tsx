/**
 * CostSummaryDrawer component tests — collapsible drawer showing cost overview.
 * Tests: three data states (loading, error, loaded), collapsed/expanded toggle,
 * spend metrics, model-mix breakdown, accessibility.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CostSummaryDrawer } from './CostSummaryDrawer';
import { fixtureCost, fixtureCostWithPricing, TESTIDS } from '../test/fixtures';
import type { CostSummary } from '../lib/types';

// Model mix with exact, known shares: 600k / 300k / 100k tokens = 60% / 30% / 10%
// of the all-time total. `daily_totals` deliberately holds a single, tiny latest
// day (5k tokens) so the pre-fix denominator (latest-day tokens x model count)
// blows every share past 100% and saturates every bar.
const fixtureCostModelMix: CostSummary = {
  ...fixtureCost,
  models: {
    'claude-haiku-4-5-20251001': {
      runs: 100,
      tokens_in: 500_000,
      tokens_out: 100_000,
      verdicts: { OK: 100, FAILED: 0, EMPTY: 0, HUNG: 0 },
    },
    'claude-sonnet-4-5-20250929': {
      runs: 20,
      tokens_in: 250_000,
      tokens_out: 50_000,
      verdicts: { OK: 20, FAILED: 0, EMPTY: 0, HUNG: 0 },
    },
    'claude-opus-4-20250805': {
      runs: 2,
      tokens_in: 80_000,
      tokens_out: 20_000,
      verdicts: { OK: 2, FAILED: 0, EMPTY: 0, HUNG: 0 },
    },
  },
  daily_totals: {
    '2026-07-30': { tokens_in: 4_000, tokens_out: 1_000 },
  },
};

/** The rendered share label for one model row, e.g. "60%". */
function modelPct(row: HTMLElement): string {
  const el = row.querySelector('.cost-summary__model-pct');
  if (!el) throw new Error('model row has no rendered percentage');
  return el.textContent ?? '';
}

function expandedDrawer(cost: CostSummary | null, status: 'live' | 'reconnecting' | 'error' = 'live') {
  const view = render(<CostSummaryDrawer cost={cost} connectionStatus={{ status }} />);
  fireEvent.click(screen.getByTestId(TESTIDS.costSummaryDrawerToggle));
  return view;
}

describe('CostSummaryDrawer', () => {
  describe('rendering & toggle', () => {
    it('renders drawer container with testid', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      expect(screen.getByTestId(TESTIDS.costSummaryDrawer)).toBeInTheDocument();
    });

    it('renders toggle button with aria-label', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      expect(toggle).toHaveAttribute('aria-label');
    });

    it('starts in collapsed state', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('aria-hidden', 'true');
    });

    it('expands when toggle is clicked', async () => {
      const user = userEvent.setup();
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      await user.click(toggle);
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('aria-hidden', 'false');
    });

    it('collapses when toggle is clicked again', async () => {
      const user = userEvent.setup();
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      await user.click(toggle);
      await user.click(toggle);
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('aria-hidden', 'true');
    });
  });

  describe('loaded state with data', () => {
    it('displays total spend metric', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCostWithPricing}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.costSummaryTotal)).toBeInTheDocument();
    });

    it('displays spend rate when available', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCostWithPricing}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      const rate = screen.getByTestId(TESTIDS.costSummaryRate);
      expect(rate).toBeInTheDocument();
    });

    it('displays model-mix breakdown', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCostWithPricing}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      const breakdown = screen.getByTestId(TESTIDS.costSummaryModelMix);
      expect(breakdown).toBeInTheDocument();
    });

    it('shows model names in breakdown', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCostWithPricing}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      // Should show at least one model name from fixture (formatted as HAIKU-4)
      const breakdown = screen.getByTestId(TESTIDS.costSummaryModelMix);
      expect(breakdown.textContent).toMatch(/HAIKU|SONNET/);
    });
  });

  describe('loading state', () => {
    it('renders loading indicator when cost is null and connection is live', () => {
      render(
        <CostSummaryDrawer
          cost={null}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.costSummaryLoading)).toBeInTheDocument();
    });
  });

  describe('error state', () => {
    it('renders error indicator when cost is null and connection is error', () => {
      render(
        <CostSummaryDrawer
          cost={null}
          connectionStatus={{ status: 'error', lastError: 'Connection failed' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.costSummaryError)).toBeInTheDocument();
    });

    it('displays error message when available', () => {
      const errorMsg = 'Backend unreachable';
      render(
        <CostSummaryDrawer
          cost={null}
          connectionStatus={{ status: 'error', lastError: errorMsg }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      expect(screen.getByText(new RegExp(errorMsg))).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('renders empty state when cost has no runs', () => {
      const emptyCost = {
        ...fixtureCost,
        overall_scorecard: { ...fixtureCost.overall_scorecard, total_runs: 0 },
      };
      render(
        <CostSummaryDrawer
          cost={emptyCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.costSummaryEmpty)).toBeInTheDocument();
    });
  });

  describe('toggle states with keyboard', () => {
    it('can toggle with Enter key', async () => {
      const user = userEvent.setup();
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      toggle.focus();
      await user.keyboard('{Enter}');
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('aria-hidden', 'false');
    });

    it('can toggle with Space key', async () => {
      const user = userEvent.setup();
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      toggle.focus();
      await user.keyboard(' ');
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('aria-hidden', 'false');
    });
  });

  describe('accessibility', () => {
    it('drawer has aria-label describing its purpose', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const drawer = screen.getByTestId(TESTIDS.costSummaryDrawer);
      expect(drawer).toHaveAttribute('aria-label');
    });

    it('toggle button has proper role (button)', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      expect(toggle.tagName).toBe('BUTTON');
    });

    it('panel uses role="status" for state changes', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const panel = screen.getByTestId(TESTIDS.costSummaryDrawerPanel);
      expect(panel).toHaveAttribute('role', 'status');
    });
  });

  describe('visual layout', () => {
    it('drawer renders with consistent structure for styling', () => {
      const { container } = render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const drawer = container.querySelector('[data-testid="cost-summary-drawer"]');
      expect(drawer).toBeInTheDocument();
      // Verify structure supports right-edge fixed positioning (CSS handles actual layout)
      expect(drawer?.querySelector('.cost-summary-drawer__toggle')).toBeInTheDocument();
      expect(drawer?.querySelector('.cost-summary-drawer__panel')).toBeInTheDocument();
    });
  });

  describe('pricing display', () => {
    it('shows cost with pricing configured', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCostWithPricing}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      const total = screen.getByTestId(TESTIDS.costSummaryTotal);
      expect(total.textContent).toMatch(/\$/);
    });

    it('shows tokens when pricing is not configured', () => {
      render(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.costSummaryDrawerToggle);
      fireEvent.click(toggle);
      const total = screen.getByTestId(TESTIDS.costSummaryTotal);
      // Token branch, not the cost branch: a token count with a K/M suffix and
      // no currency symbol. (`/\d+[KMG]?/` matched "$0.00" too, so it could not
      // tell the two branches apart.)
      expect(total.textContent).not.toMatch(/\$/);
      expect(total.textContent).toMatch(/\d+(\.\d+)?[KM]\b/);
    });
  });

  // --- Regression: P1-a1 -------------------------------------------------
  // useSSE retains the last `cost` payload when the EventSource errors
  // (lib/useSSE.ts error handler spreads `prev`). Gating isError on `!cost`
  // therefore made the error state unreachable after the first payload, and the
  // drawer showed stale spend forever with no indication. Connection status must
  // win over cached data for the error determination.
  describe('stale data after the connection drops', () => {
    it('leaves the healthy loaded branch when the stream stops being live', () => {
      const { rerender } = expandedDrawer(fixtureCost, 'live');
      // Precondition: a healthy, loaded render.
      expect(screen.getByTestId(TESTIDS.costSummaryTotal)).toBeInTheDocument();

      // Backend dies; useSSE keeps the last cost and flips the status.
      rerender(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'reconnecting', lastError: 'Reconnecting (attempt 1)' }}
        />
      );

      expect(screen.queryByTestId(TESTIDS.costSummaryTotal)).toBeNull();
      expect(screen.queryByTestId(TESTIDS.costSummaryRate)).toBeNull();
      expect(screen.queryByTestId(TESTIDS.costSummaryModelMix)).toBeNull();
    });

    it('surfaces the error state when data is cached but the stream is down', () => {
      const { rerender } = expandedDrawer(fixtureCost, 'live');
      rerender(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'reconnecting', lastError: 'Reconnecting (attempt 1)' }}
        />
      );
      expect(screen.getByTestId(TESTIDS.costSummaryError)).toBeInTheDocument();
      expect(screen.getByText(/Reconnecting \(attempt 1\)/)).toBeInTheDocument();
    });

    it('marks the retained values as stale rather than presenting them as current', () => {
      const { rerender } = expandedDrawer(fixtureCost, 'live');
      rerender(
        <CostSummaryDrawer
          cost={fixtureCost}
          connectionStatus={{ status: 'error', lastError: 'Connection lost' }}
        />
      );
      const stale = screen.getByTestId(TESTIDS.costSummaryStale);
      expect(stale.textContent).toMatch(/stale/i);
      // The last-known number is still shown (operators want it), but only under
      // the stale label.
      expect(stale.textContent).toMatch(/\d/);
    });

    it('shows no stale block when the stream is down and nothing was ever received', () => {
      expandedDrawer(null, 'reconnecting');
      expect(screen.getByTestId(TESTIDS.costSummaryError)).toBeInTheDocument();
      expect(screen.queryByTestId(TESTIDS.costSummaryStale)).toBeNull();
    });

    it('never renders the empty state while the stream is down', () => {
      const emptyCost = {
        ...fixtureCost,
        overall_scorecard: { ...fixtureCost.overall_scorecard, total_runs: 0 },
      };
      expandedDrawer(emptyCost, 'error');
      expect(screen.queryByTestId(TESTIDS.costSummaryEmpty)).toBeNull();
      expect(screen.getByTestId(TESTIDS.costSummaryError)).toBeInTheDocument();
    });
  });

  // --- Regression: P1-a2 -------------------------------------------------
  // The share was all-time model tokens / (latest-day tokens x model count) --
  // numerator and denominator from different windows, unbounded above, then
  // clamped by `width: min(pct * 2, 100)%` so every bar rendered full.
  describe('model-mix proportions', () => {
    it('renders each model at its true share of total tokens', () => {
      expandedDrawer(fixtureCostModelMix);
      const rows = screen.getAllByTestId(TESTIDS.costSummaryModelRow);
      expect(rows).toHaveLength(3);
      expect(rows[0].textContent).toMatch(/HAIKU/);
      expect(rows.map(modelPct)).toEqual(['60%', '30%', '10%']);
    });

    it('sizes each bar to the model share, not to a saturated clamp', () => {
      const { container } = expandedDrawer(fixtureCostModelMix);
      const fills = Array.from(
        container.querySelectorAll<HTMLElement>('.cost-summary__model-fill')
      );
      expect(fills.map((f) => f.style.width)).toEqual(['60%', '30%', '10%']);
    });

    it('produces shares that sum to 100% across all models', () => {
      expandedDrawer(fixtureCostModelMix);
      const rows = screen.getAllByTestId(TESTIDS.costSummaryModelRow);
      const total = rows.reduce((sum, row) => sum + parseFloat(modelPct(row)), 0);
      expect(total).toBeCloseTo(100, 1);
    });

    it('labels the window the shares are computed over', () => {
      expandedDrawer(fixtureCostModelMix);
      const breakdown = screen.getByTestId(TESTIDS.costSummaryModelMix);
      expect(breakdown.textContent).toMatch(/all-time/i);
    });

    it('renders no breakdown when no model has recorded tokens', () => {
      const noTokens: CostSummary = {
        ...fixtureCostModelMix,
        models: {
          'claude-haiku-4-5-20251001': {
            runs: 1,
            tokens_in: 0,
            tokens_out: 0,
            verdicts: { OK: 1, FAILED: 0, EMPTY: 0, HUNG: 0 },
          },
        },
      };
      expandedDrawer(noTokens);
      expect(screen.queryByTestId(TESTIDS.costSummaryModelMix)).toBeNull();
    });
  });
});
