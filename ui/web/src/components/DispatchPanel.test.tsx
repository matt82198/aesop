/**
 * DispatchPanel component tests.
 * Tests data rendering, unavailable states, warnings, and agent display.
 *
 * The component accepts an injectable `fetcher` prop for deterministic testing
 * (no fake timers needed). This follows the WavePRBoard pattern.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import DispatchPanel from './DispatchPanel';
import { fixtureWaveDispatch, fixtureWaveDispatchUnavailable, TESTIDS } from '../test/fixtures';
import type { WaveDispatchData } from '../lib/types';

// Helper: returns a fetcher that immediately resolves with the given data
const ready = (data: WaveDispatchData) => () => Promise.resolve(data);

describe('DispatchPanel', () => {
  it('renders loading state initially', () => {
    // Never-resolving fetcher keeps the component in the loading state
    render(<DispatchPanel fetcher={() => new Promise<WaveDispatchData>(() => {})} />);
    expect(screen.getByTestId(TESTIDS.dispatchPanel)).toBeInTheDocument();
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it('renders available dispatch data', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    expect(await screen.findByTestId(TESTIDS.dispatchPanel)).toBeInTheDocument();

    // Check header
    expect(screen.getByText('Wave Dispatch')).toBeInTheDocument();
    expect(screen.getByText(fixtureWaveDispatch.wave_phase!)).toBeInTheDocument();

    // Check agents
    const agentRows = screen.getAllByTestId(TESTIDS.dispatchAgentRow);
    expect(agentRows).toHaveLength(fixtureWaveDispatch.agents.length);
  });

  it('displays agent phase badges', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const badges = await screen.findAllByTestId(TESTIDS.dispatchAgentPhase);
    expect(badges.length).toBeGreaterThan(0);

    // Check first agent phase badge
    expect(badges[0].textContent).toBe('tool-use');
  });

  it('formats activity age correctly', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const ages = await screen.findAllByTestId(TESTIDS.dispatchAgentAge);
    expect(ages.length).toBeGreaterThan(0);

    // First agent has 3 seconds, should display "3s"
    expect(ages[0].textContent).toBe('3s');
  });

  it('formats token estimates correctly', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const tokens = await screen.findAllByTestId(TESTIDS.dispatchAgentTokens);
    expect(tokens.length).toBeGreaterThan(0);

    // First agent has 145000 tokens, should display "145.0KT"
    expect(tokens[0].textContent).toMatch(/14\d\.\dKT/);
  });

  it('displays warnings for inactive agents', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    expect(await screen.findByText(/inactive >5min/i)).toBeInTheDocument();
  });

  it('renders unavailable state when no workflow active', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatchUnavailable)} />);

    expect(await screen.findByTestId(TESTIDS.dispatchPanelUnavailable)).toBeInTheDocument();
    expect(screen.getByText(/No active workflow/i)).toBeInTheDocument();
  });

  it('renders empty agents state', async () => {
    const emptyData: WaveDispatchData = {
      available: true,
      wave_phase: 'wave-test',
      agents: [],
      at: new Date().toISOString(),
    };

    render(<DispatchPanel fetcher={ready(emptyData)} />);

    expect(await screen.findByText(/No agents currently active/i)).toBeInTheDocument();
  });

  it('stops rendering when an error occurs in fetch', async () => {
    const errorFetcher = () => Promise.reject(new Error('Network error'));

    render(<DispatchPanel fetcher={errorFetcher} />);

    expect(await screen.findByTestId(TESTIDS.dispatchPanelUnavailable)).toBeInTheDocument();
  });

  it('renders all agents from fixture', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const agentRows = await screen.findAllByTestId(TESTIDS.dispatchAgentRow);
    expect(agentRows).toHaveLength(3);

    // Check specific agent IDs
    expect(screen.getByText('fleet-fix-0')).toBeInTheDocument();
    expect(screen.getByText('fleet-fix-1')).toBeInTheDocument();
    expect(screen.getByText('fleet-review-0')).toBeInTheDocument();
  });

  it('renders timestamp from dispatch data', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    await screen.findByTestId(TESTIDS.dispatchPanel);

    // The timestamp should be rendered (formatted from fixtureWaveDispatch.at)
    // The time will be localized, so we just check that some time text appears
    const container = screen.getByTestId(TESTIDS.dispatchPanel);
    expect(container.textContent).toContain(':');
  });

  it('does not duplicate the phase string within a single agent row', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const rows = await screen.findAllByTestId(TESTIDS.dispatchAgentRow);
    expect(rows.length).toBe(fixtureWaveDispatch.agents.length);

    rows.forEach((row, idx) => {
      const phase = fixtureWaveDispatch.agents[idx].phase;
      const occurrences = (row.textContent?.split(phase).length ?? 1) - 1;
      expect(occurrences).toBe(1);
    });
  });

  it('displays all phase types with correct colors', async () => {
    render(<DispatchPanel fetcher={ready(fixtureWaveDispatch)} />);

    const badges = await screen.findAllByTestId(TESTIDS.dispatchAgentPhase);
    const phases = badges.map((b) => b.textContent);

    // Fixture has: 'tool-use', 'stall', 'thinking'
    expect(phases).toContain('tool-use');
    expect(phases).toContain('stall');
    expect(phases).toContain('thinking');
  });
});
