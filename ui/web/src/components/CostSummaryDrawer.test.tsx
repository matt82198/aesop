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
      // Should show token count with K/M/G suffix or raw number
      expect(total.textContent).toMatch(/\d+(\.\d+)?[KMG]?/i);
    });
  });
});
