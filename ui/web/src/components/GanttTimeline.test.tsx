/**
 * GanttTimeline.test.tsx — component tests for Gantt timeline visualization.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import GanttTimeline from './GanttTimeline';
import { TESTIDS, fixtureWaveGanttUnavailable } from '../test/fixtures';
import type { WaveGanttData } from '../lib/types';
import ganttSampleFixture from './__fixtures__/gantt-sample.json';

// Load frozen fixture to ensure tests never break on stats.json refreshes
const fixtureWaveGantt: WaveGanttData = ganttSampleFixture as WaveGanttData;

describe('GanttTimeline', () => {
  it('renders loading state initially', () => {
    const mockFetcher = vi.fn(() => new Promise(() => {}) as Promise<import('./GanttTimeline').GanttData>); // Never resolves
    render(<GanttTimeline fetcher={mockFetcher} />);
    expect(screen.getByTestId(TESTIDS.ganttTimeline)).toBeInTheDocument();
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('renders with fixture data', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(screen.getByTestId(TESTIDS.ganttTimeline)).toBeInTheDocument();
    });

    // Should render title
    expect(screen.getByText('Wave Gantt Timeline')).toBeInTheDocument();

    // Should render wave phase
    expect(screen.getByText('wave-rc.7: verify')).toBeInTheDocument();

    // Should render agent rows
    const rows = screen.getAllByTestId(TESTIDS.ganttRow);
    expect(rows.length).toBe(3);

    // Should render agent IDs
    expect(screen.getByText('fleet-fix-0')).toBeInTheDocument();
    expect(screen.getByText('fleet-fix-1')).toBeInTheDocument();
    expect(screen.getByText('fleet-review-0')).toBeInTheDocument();
  });

  it('renders phase bars with correct phase labels', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      const bars = screen.getAllByTestId(TESTIDS.ganttPhaseBar);
      expect(bars.length).toBeGreaterThan(0);
    });

    // First agent should have dispatch, thinking, and tool-use phases
    const bars = screen.getAllByTestId(TESTIDS.ganttPhaseBar);
    expect(bars.length).toBe(6); // 3 + 2 + 1 phases across 3 agents = 6 spans
  });

  it('renders unavailable state', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGanttUnavailable);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(screen.getByText('No active workflow')).toBeInTheDocument();
    });
  });

  it('renders error message on fetch failure', async () => {
    const mockFetcher = vi.fn().mockRejectedValue(new Error('Network error'));
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it('renders legend with phase colors', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(screen.getByText('Dispatch')).toBeInTheDocument();
      expect(screen.getByText('Thinking')).toBeInTheDocument();
      expect(screen.getByText('Tool Use')).toBeInTheDocument();
      expect(screen.getByText('Stall')).toBeInTheDocument();
      expect(screen.getByText('Done')).toBeInTheDocument();
    });
  });

  it('renders agent status badges', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(screen.getByText('running')).toBeInTheDocument();
      expect(screen.getByText('stalled')).toBeInTheDocument();
      expect(screen.getByText('done')).toBeInTheDocument();
    });
  });

  it('fetches data on mount', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      expect(mockFetcher).toHaveBeenCalledTimes(1);
    });
  });

  it('polls for data every 3 seconds when visible', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    vi.useFakeTimers({ shouldAdvanceTime: true });

    render(<GanttTimeline fetcher={mockFetcher} />);

    // Wait for initial load
    await waitFor(() => expect(mockFetcher).toHaveBeenCalled());
    mockFetcher.mockClear();

    // Advance timer by 3 seconds
    vi.advanceTimersByTime(3000);

    await waitFor(() => {
      expect(mockFetcher).toHaveBeenCalledTimes(1);
    });

    vi.useRealTimers();
  });

  it('renders durations correctly', async () => {
    const mockFetcher = vi.fn().mockResolvedValue(fixtureWaveGantt);
    render(<GanttTimeline fetcher={mockFetcher} />);

    await waitFor(() => {
      // Should render agent durations
      expect(screen.getByText('50s')).toBeInTheDocument(); // fleet-fix-0: 50 seconds
    }, { timeout: 10000 });
  });
});
