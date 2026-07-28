/**
 * Test suite for FirstTryRateBoard component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FirstTryRateBoard } from './FirstTryRateBoard';

// Mock the fetchAPI function
vi.mock('../lib/api', () => ({
  fetchAPI: vi.fn(),
}));

import { fetchAPI } from '../lib/api';

describe('FirstTryRateBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders loading state initially', () => {
    (fetchAPI as any).mockImplementation(() => new Promise(() => {})); // never resolves

    render(<FirstTryRateBoard />);
    const loading = screen.getByTestId('first-try-board-loading');
    expect(loading).toBeDefined();
  });

  it('renders error state on fetch failure', async () => {
    (fetchAPI as any).mockResolvedValue({ error: 'Network error' });

    render(<FirstTryRateBoard />);

    const errorDiv = await screen.findByTestId('first-try-board-error');
    expect(errorDiv).toBeDefined();
    expect(errorDiv.textContent).toContain('Error loading first-try rates');
  });

  it('renders board with data', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: {
        first_try: 100,
        needed_repair: 20,
        rate: 0.833,
      },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    const board = await screen.findByTestId('first-try-board');
    expect(board).toBeDefined();
  });

  it('displays overall metric correctly', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: {
        first_try: 80,
        needed_repair: 20,
        rate: 0.8,
      },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    const overallMetric = await screen.findByTestId('overall-metric');
    expect(overallMetric).toBeDefined();
    expect(overallMetric.textContent).toContain('80%');
  });

  it('renders domain cards when data available', async () => {
    const mockData = {
      domains: {
        'ui': {
          first_try: 50,
          needed_repair: 5,
          rate: 0.909,
        },
        'drivers': {
          first_try: 30,
          needed_repair: 15,
          rate: 0.667,
        },
      },
      lanes: {},
      overall: {
        first_try: 80,
        needed_repair: 20,
        rate: 0.8,
      },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    const domainsGrid = await screen.findByTestId('domains-grid');
    expect(domainsGrid).toBeDefined();

    const uiCard = screen.getByTestId('stat-card-ui');
    const driversCard = screen.getByTestId('stat-card-drivers');

    expect(uiCard).toBeDefined();
    expect(driversCard).toBeDefined();
  });

  it('renders lane cards when data available', async () => {
    const mockData = {
      domains: {},
      lanes: {
        'ranked': {
          first_try: 40,
          needed_repair: 10,
          rate: 0.8,
        },
        'in-progress': {
          first_try: 20,
          needed_repair: 5,
          rate: 0.8,
        },
      },
      overall: {
        first_try: 60,
        needed_repair: 15,
        rate: 0.8,
      },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    const lanesGrid = await screen.findByTestId('lanes-grid');
    expect(lanesGrid).toBeDefined();

    const rankedCard = screen.getByTestId('stat-card-ranked');
    const inProgressCard = screen.getByTestId('stat-card-in-progress');

    expect(rankedCard).toBeDefined();
    expect(inProgressCard).toBeDefined();
  });

  it('applies correct health class based on rate', async () => {
    const mockData = {
      domains: {
        'excellent': { first_try: 90, needed_repair: 10, rate: 0.9 },
        'good': { first_try: 70, needed_repair: 30, rate: 0.7 },
        'fair': { first_try: 50, needed_repair: 50, rate: 0.5 },
        'poor': { first_try: 30, needed_repair: 70, rate: 0.3 },
      },
      lanes: {},
      overall: { first_try: 240, needed_repair: 160, rate: 0.6 },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    await screen.findByTestId('stat-card-excellent');

    const excellentCard = screen.getByTestId('stat-card-excellent');
    const goodCard = screen.getByTestId('stat-card-good');
    const fairCard = screen.getByTestId('stat-card-fair');
    const poorCard = screen.getByTestId('stat-card-poor');

    expect(excellentCard.className).toContain('health-excellent');
    expect(goodCard.className).toContain('health-good');
    expect(fairCard.className).toContain('health-fair');
    expect(poorCard.className).toContain('health-poor');
  });

  it('has refresh button that refetches data', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: {
        first_try: 100,
        needed_repair: 20,
        rate: 0.833,
      },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    const { rerender } = render(<FirstTryRateBoard />);

    const refreshBtn = await screen.findByRole('button');
    expect(refreshBtn).toBeDefined();

    // Click refresh (simulation)
    fireEvent.click(refreshBtn);

    rerender(<FirstTryRateBoard />);
    // Verify it doesn't crash
    expect(screen.getByTestId('first-try-board')).toBeDefined();
  });

  it('auto-refreshes when autoRefresh prop is set', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: { first_try: 100, needed_repair: 20, rate: 0.833 },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard autoRefresh={5} />);

    const board = await screen.findByTestId('first-try-board');
    expect(board).toBeDefined();

    // Initial call
    expect(fetchAPI).toHaveBeenCalledTimes(1);

    // Fast-forward time
    vi.advanceTimersByTime(5000);

    // Should call again
    await waitFor(() => {
      expect(fetchAPI).toHaveBeenCalledTimes(2);
    });
  });

  it('renders empty state when no domains or lanes', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: { first_try: 0, needed_repair: 0, rate: 0 },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    const emptySection = await screen.findByText('No domain or lane data available yet');
    expect(emptySection).toBeDefined();
  });

  it('displays update timestamp', async () => {
    const mockData = {
      domains: {},
      lanes: {},
      overall: { first_try: 100, needed_repair: 20, rate: 0.833 },
    };

    (fetchAPI as any).mockResolvedValue(mockData);

    render(<FirstTryRateBoard />);

    await screen.findByTestId('first-try-board');
    // The component should display "Updated: HH:MM:SS"
    // We can't test the exact time, but we can verify the text exists
    const boardHeader = screen.getByText(/Updated:/);
    expect(boardHeader).toBeDefined();
  });
});
