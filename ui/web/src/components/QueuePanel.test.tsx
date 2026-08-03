/**
 * QueuePanel component tests — merge-queue operator panel.
 * Tests: three data states (loading, error, loaded), expanded/collapsed toggle,
 * queue metrics display, exception rows, accessibility.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueuePanel } from './QueuePanel';
import { fixtureQueueData, fixtureQueueDataDegraded, fixtureQueueDataEmpty, TESTIDS } from '../test/fixtures';

describe('QueuePanel', () => {
  describe('rendering & toggle', () => {
    it('renders panel container with testid', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      expect(screen.getByTestId(TESTIDS.queuePanel)).toBeInTheDocument();
    });

    it('renders toggle button with aria-label', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      expect(toggle).toHaveAttribute('aria-label');
    });

    it('starts in collapsed state', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('aria-hidden', 'true');
    });

    it('expands when toggle is clicked', async () => {
      const user = userEvent.setup();
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      await user.click(toggle);
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('aria-hidden', 'false');
    });

    it('collapses when toggle is clicked again', async () => {
      const user = userEvent.setup();
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      await user.click(toggle);
      await user.click(toggle);
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('aria-hidden', 'true');
    });
  });

  describe('loaded state with data', () => {
    it('displays queue depth metric', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const depth = screen.getByTestId(TESTIDS.queueDepth);
      expect(depth).toBeInTheDocument();
      expect(depth.textContent).toContain('8');
    });

    it('displays batch state metric', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const batch = screen.getByTestId(TESTIDS.queueBatchState);
      expect(batch).toBeInTheDocument();
      expect(batch.textContent).toContain('2');
    });

    it('displays last advance age', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const advance = screen.getByTestId(TESTIDS.queueLastAdvance);
      expect(advance).toBeInTheDocument();
    });

    it('displays exception rows', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const exceptionsList = screen.getByTestId(TESTIDS.queueExceptionsList);
      expect(exceptionsList).toBeInTheDocument();
      const rows = screen.getAllByTestId(TESTIDS.queueExceptionRow);
      expect(rows.length).toBeGreaterThan(0);
    });

    it('shows exception details (ts, pr, kind)', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      // First exception: PR 743, ci_failure
      expect(screen.getByText(/743/)).toBeInTheDocument();
      expect(screen.getByText(/ci_failure/)).toBeInTheDocument();
    });

    it('shows degraded styling when last advance > 10min', () => {
      render(
        <QueuePanel
          queue={fixtureQueueDataDegraded}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const advance = screen.getByTestId(TESTIDS.queueLastAdvance);
      expect(advance).toHaveClass('queue-panel__last-advance--degraded');
    });
  });

  describe('loading state', () => {
    it('renders loading indicator when queue is null and connection is live', () => {
      render(
        <QueuePanel
          queue={null}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.queueLoading)).toBeInTheDocument();
    });
  });

  describe('error state', () => {
    it('renders error indicator when queue is null and connection is error', () => {
      render(
        <QueuePanel
          queue={null}
          connectionStatus={{ status: 'error', lastError: 'Connection failed' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.queueError)).toBeInTheDocument();
    });

    it('displays error message when available', () => {
      const errorMsg = 'Queue API unreachable';
      render(
        <QueuePanel
          queue={null}
          connectionStatus={{ status: 'error', lastError: errorMsg }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      expect(screen.getByText(new RegExp(errorMsg))).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('renders empty state when queue has no PRs and no exceptions', () => {
      render(
        <QueuePanel
          queue={fixtureQueueDataEmpty}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      expect(screen.getByTestId(TESTIDS.queueEmpty)).toBeInTheDocument();
    });

    it('shows "queue idle" message in empty state', () => {
      render(
        <QueuePanel
          queue={fixtureQueueDataEmpty}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      expect(screen.getByText(/queue idle/i)).toBeInTheDocument();
    });
  });

  describe('toggle states with keyboard', () => {
    it('can toggle with Enter key', async () => {
      const user = userEvent.setup();
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      toggle.focus();
      await user.keyboard('{Enter}');
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('aria-hidden', 'false');
    });

    it('can toggle with Space key', async () => {
      const user = userEvent.setup();
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      toggle.focus();
      await user.keyboard(' ');
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('aria-hidden', 'false');
    });
  });

  describe('accessibility', () => {
    it('panel has aria-label describing its purpose', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const panel = screen.getByTestId(TESTIDS.queuePanel);
      expect(panel).toHaveAttribute('aria-label');
    });

    it('toggle button has proper role (button)', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      expect(toggle.tagName).toBe('BUTTON');
    });

    it('content uses role="status" for state changes', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const content = screen.getByTestId(TESTIDS.queuePanelContent);
      expect(content).toHaveAttribute('role', 'status');
    });
  });

  describe('visual layout', () => {
    it('panel renders with consistent structure for styling', () => {
      const { container } = render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const panel = container.querySelector('[data-testid="queue-panel"]');
      expect(panel).toBeInTheDocument();
      expect(panel?.querySelector('.queue-panel__toggle')).toBeInTheDocument();
      expect(panel?.querySelector('.queue-panel__content')).toBeInTheDocument();
    });
  });

  describe('age formatting', () => {
    it('formats age in seconds correctly', () => {
      render(
        <QueuePanel
          queue={fixtureQueueData}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const advance = screen.getByTestId(TESTIDS.queueLastAdvance);
      // 180 seconds = 3 minutes
      expect(advance.textContent).toMatch(/3m|180s/);
    });

    it('shows degraded formatting for age > 10min', () => {
      const { container } = render(
        <QueuePanel
          queue={fixtureQueueDataDegraded}
          connectionStatus={{ status: 'live' }}
        />
      );
      const toggle = screen.getByTestId(TESTIDS.queuePanelToggle);
      fireEvent.click(toggle);
      const advance = container.querySelector('.queue-panel__last-advance--degraded');
      expect(advance).toBeInTheDocument();
    });
  });
});
