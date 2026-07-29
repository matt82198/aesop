/**
 * WaveTelemetryProgress tests — polling contract and display logic.
 *
 * Contract:
 * - Fetches on mount and polls every 5s (POLL_INTERVAL_MS = 5000)
 * - Accepts an injectable fetcher for tests (dependency injection)
 * - Displays wave, phase, and top blocker
 * - No fake-timer traps — uses real setInterval with manual cleanup
 *
 * Run: npm test -- WaveTelemetryProgress.test.tsx
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TESTIDS } from '../test/fixtures';
import { WaveTelemetryProgress } from './WaveTelemetryProgress';

// Helper: returns a fetcher that immediately resolves with the given data
const ready = (data: WaveTelemetry) => () => Promise.resolve(data);

interface WaveTelemetry {
  wave: string;
  phase: string;
  blocker: string;
  tokens_used: number;
  top_model: string;
  ok_rate: number;
}

const fixtureData: WaveTelemetry = {
  wave: 'wave-rc.2',
  phase: 'rc-1-published-source-available',
  blocker: 'Dashboard wave telemetry tile',
  tokens_used: 1500000,
  top_model: 'haiku',
  ok_rate: 0.95,
};

describe('WaveTelemetryProgress', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders and fetches on mount', async () => {
    const fetcher = vi.fn(ready(fixtureData));

    render(<WaveTelemetryProgress fetcher={fetcher} />);

    // Should fetch immediately on mount
    expect(fetcher).toHaveBeenCalledWith('/api/wave/telemetry');
    expect(fetcher).toHaveBeenCalledTimes(1);

    // Should show the data after fetch completes (phase is formatted as "Rc (rc-1-published-source-available)")
    expect(await screen.findByText('wave-rc.2')).toBeInTheDocument();
  });

  it('displays wave, phase, and blocker data', async () => {
    render(<WaveTelemetryProgress fetcher={ready(fixtureData)} />);

    expect(await screen.findByText('wave-rc.2')).toBeInTheDocument();
    // formatPhaseForDisplay formats "rc-1-published-source-available" as "Rc (rc-1-published-source-available)"
    expect(screen.getByText(/Rc \(rc-1-published-source-available\)/)).toBeInTheDocument();
    expect(screen.getByText('Dashboard wave telemetry tile')).toBeInTheDocument();
  });

  it('has proper test id', async () => {
    render(<WaveTelemetryProgress fetcher={ready(fixtureData)} />);

    expect(await screen.findByTestId(TESTIDS.waveTelemetryProgress)).toBeInTheDocument();
  });

  it('shows loading state on initial mount', () => {
    // Never-resolving fetcher keeps the component in the loading state
    render(<WaveTelemetryProgress fetcher={() => new Promise<WaveTelemetry>(() => {})} />);

    expect(screen.getByText('Loading wave telemetry...')).toBeInTheDocument();
  });

  it('shows error state when fetch fails', async () => {
    const fetcher = () => Promise.reject(new Error('Network error'));

    render(<WaveTelemetryProgress fetcher={fetcher} />);

    expect(await screen.findByText(/Error: Network error/)).toBeInTheDocument();
  });

  it('cleans up polling interval on unmount', async () => {
    // Verify that the component sets up and cleans up the interval properly
    // by checking no console errors occur on unmount
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { unmount } = render(<WaveTelemetryProgress fetcher={ready(fixtureData)} />);

    await screen.findByText('Dashboard wave telemetry tile');
    unmount();

    // No errors should have been logged during cleanup
    expect(consoleErrorSpy).not.toHaveBeenCalled();
    consoleErrorSpy.mockRestore();
  });

  it('shows verification pass rate as a percentage when runs exist', async () => {
    render(<WaveTelemetryProgress fetcher={ready(fixtureData)} />);

    // fixtureData: tokens_used=1500000 (>0, so "has runs"), ok_rate=0.95 -> 95%
    expect(await screen.findByText('95%')).toBeInTheDocument();
  });

  it('shows an honest empty-state for pass rate when no runs have happened yet', async () => {
    const noRunsData: WaveTelemetry = { ...fixtureData, tokens_used: 0, ok_rate: 0.0 };
    render(<WaveTelemetryProgress fetcher={ready(noRunsData)} />);

    expect(await screen.findByText('n/a — no runs yet')).toBeInTheDocument();
  });

  it('renders "No blocker recorded" instead of the raw "unknown" sentinel', async () => {
    const noBlockerData: WaveTelemetry = { ...fixtureData, blocker: 'unknown' };
    render(<WaveTelemetryProgress fetcher={ready(noBlockerData)} />);

    expect(await screen.findByText('No blocker recorded')).toBeInTheDocument();
  });

  it('uses default fetcher when none provided', () => {
    // This should not throw type errors when no fetcher is provided
    expect(() => {
      const Component = () => {
        return <WaveTelemetryProgress />;
      };
      render(<Component />);
    }).not.toThrow();
  });
});
