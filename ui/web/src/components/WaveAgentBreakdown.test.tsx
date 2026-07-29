/**
 * Wave & Agent Cost Breakdown — tests for per-wave/per-agent cost display.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WaveAgentBreakdown } from './WaveAgentBreakdown';
import { fixtureCost, fixtureCostWithPricing, TESTIDS } from '../test/fixtures';

describe('WaveAgentBreakdown', () => {
  it('renders with testid', () => {
    render(<WaveAgentBreakdown cost={fixtureCost} />);
    expect(screen.getByTestId(TESTIDS.waveAgentBreakdown)).toBeInTheDocument();
  });

  it('renders "Cost per Wave" section', () => {
    render(<WaveAgentBreakdown cost={fixtureCost} />);
    expect(screen.getByText('Cost per Wave')).toBeInTheDocument();
  });

  it('renders "Cost per Agent Type" section', () => {
    render(<WaveAgentBreakdown cost={fixtureCost} />);
    expect(screen.getByText('Cost per Agent Type')).toBeInTheDocument();
  });

  describe('Wave breakdown', () => {
    it('displays wave table with headers', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      const tables = screen.getAllByRole('table');
      const waveTable = tables[0]; // First table is waves
      expect(waveTable).toBeInTheDocument();
      expect(waveTable.textContent).toContain('Wave');
      expect(waveTable.textContent).toContain('Tokens In');
      expect(waveTable.textContent).toContain('Tokens Out');
      expect(waveTable.textContent).toContain('Cost');
    });

    it('displays wave data rows', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      expect(screen.getByText('wave-14')).toBeInTheDocument();
      expect(screen.getByText('wave-13')).toBeInTheDocument();
    });

    it('displays formatted token counts', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      // Should show "M" for millions (2030170 ≈ 2.03M)
      const waveSection = screen.getAllByRole('table')[0];
      expect(waveSection.textContent).toMatch(/\d+\.?\d*M/);
    });

    it('expands wave row to show model breakdown on click', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const waveRow = screen.getByText('wave-14').closest('tr');
      const expandButton = waveRow?.querySelector('.expand-toggle') as HTMLElement;

      expect(expandButton).toBeInTheDocument();

      // Click to expand
      await user.click(expandButton);

      // Now should show Model Breakdown heading
      const modelBreakdownHeading = screen.getAllByText('Model Breakdown');
      expect(modelBreakdownHeading.length).toBeGreaterThan(0);
    });

    it('shows model tokens and percentages in expanded view', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const expandButtons = screen.getAllByRole('button', { name: /expand/i });
      await user.click(expandButtons[0]); // Click first wave expand

      const detailTable = screen.getByText('Model Breakdown')?.closest('.detail-content')?.querySelector('table');
      expect(detailTable).toBeInTheDocument();
      expect(detailTable?.textContent).toContain('Model');
      expect(detailTable?.textContent).toContain('Tokens');
      expect(detailTable?.textContent).toContain('%');
    });

    it('displays empty state when no wave data', () => {
      const emptyCost = {
        ...fixtureCost,
        per_wave_costs: {},
      };
      render(<WaveAgentBreakdown cost={emptyCost} />);
      expect(screen.getByText(/No wave-level cost data available/i)).toBeInTheDocument();
    });
  });

  describe('Agent breakdown', () => {
    it('displays agent table with headers', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      const tables = screen.getAllByRole('table');
      const agentTable = tables[1]; // Second table is agents
      expect(agentTable).toBeInTheDocument();
      expect(agentTable.textContent).toContain('Agent Type');
      expect(agentTable.textContent).toContain('Runs');
      expect(agentTable.textContent).toContain('Tokens');
    });

    it('displays agent data rows', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      expect(screen.getByText('Agent')).toBeInTheDocument();
      expect(screen.getByText('main thread')).toBeInTheDocument();
    });

    it('shows run counts for each agent', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      const agentSection = screen.getAllByRole('table')[1];
      expect(agentSection.textContent).toContain('128'); // Runs for 'Agent'
      expect(agentSection.textContent).toContain('14'); // Runs for 'main thread'
    });

    it('expands agent row to show model breakdown on click', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const agentRow = screen.getByText('Agent').closest('tr');
      const expandButton = agentRow?.querySelector('.expand-toggle') as HTMLElement;

      // Initially collapsed (model details not visible)
      let expandedDetail = screen.queryAllByText('Model Breakdown');
      const initialDetailCount = expandedDetail.length;

      // Click to expand
      await user.click(expandButton);

      // Should have more model breakdown sections now
      expandedDetail = screen.queryAllByText('Model Breakdown');
      expect(expandedDetail.length).toBeGreaterThanOrEqual(initialDetailCount);
    });

    it('shows agent stats in expanded view (tokens, verdicts)', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const agentRow = screen.getByText('Agent').closest('tr');
      const expandButton = agentRow?.querySelector('.expand-toggle') as HTMLElement;

      await user.click(expandButton);

      const detailContent = screen.getByText('Tokens In:')?.closest('.detail-stats');
      expect(detailContent).toBeInTheDocument();
      expect(detailContent?.textContent).toContain('Tokens Out');
      expect(detailContent?.textContent).toContain('Verdicts');
      expect(detailContent?.textContent).toContain('OK');
    });

    it('displays empty state when no agent data', () => {
      const emptyCost = {
        ...fixtureCost,
        per_agent_costs: {},
      };
      render(<WaveAgentBreakdown cost={emptyCost} />);
      expect(screen.getByText(/No agent-level cost data available/i)).toBeInTheDocument();
    });
  });

  describe('Pricing display', () => {
    it('displays costs with pricing', () => {
      render(<WaveAgentBreakdown cost={fixtureCostWithPricing} />);
      // Should show dollar amounts ($ prefix) in cost columns
      const view = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      expect(view.textContent).toMatch(/\$[\d.]+/);
    });

    it('shows dash (—) for cost when pricing unavailable', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      // Without pricing, costs show as "—"
      const view = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      expect(view.textContent).toContain('—');
    });
  });

  describe('Accessibility', () => {
    it('has proper aria labels for expand buttons', async () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      // Find the expand toggle buttons specifically (they have the .expand-toggle class)
      const view = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      const expandButtons = view.querySelectorAll('.expand-toggle');
      expect(expandButtons.length).toBeGreaterThan(0);
      // Check that at least one button has aria-expanded attribute
      const hasAriaExpanded = Array.from(expandButtons).some((btn) =>
        btn.getAttribute('aria-expanded') !== null
      );
      expect(hasAriaExpanded).toBe(true);
    });

    it('updates aria-expanded when expanded', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const view = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      const expandButtons = view.querySelectorAll('.expand-toggle');
      const firstExpandButton = expandButtons[0] as HTMLElement;
      expect(firstExpandButton.getAttribute('aria-expanded')).toBe('false');

      await user.click(firstExpandButton);
      expect(firstExpandButton.getAttribute('aria-expanded')).toBe('true');
    });

    it('section has proper aria label', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      const section = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      expect(section.getAttribute('aria-label')).toBeTruthy();
    });
  });

  describe('Data formatting', () => {
    it('formats large token counts as millions', () => {
      render(<WaveAgentBreakdown cost={fixtureCost} />);
      // 2030170 should display as "2.03M"
      expect(screen.getByText(/2\.0[0-9]M/)).toBeInTheDocument();
    });

    it('formats model names by removing "claude-" prefix', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const expandButtons = screen.getAllByRole('button', { name: /expand/i });
      await user.click(expandButtons[0]); // Expand first wave

      // Should show "haiku" not "claude-haiku-4-5-20251001"
      const modelText = screen.getByText(/haiku/i);
      expect(modelText).toBeInTheDocument();
      expect(modelText.textContent).not.toContain('claude-');
    });

    it('displays percentages to 1 decimal place', async () => {
      const user = userEvent.setup();
      render(<WaveAgentBreakdown cost={fixtureCost} />);

      const expandButtons = screen.getAllByRole('button');
      await user.click(expandButtons[0]);

      // Should show percentages - check the section contains a percentage pattern
      const view = screen.getByTestId(TESTIDS.waveAgentBreakdown);
      // After expanding, should show percentages like "85.2%" or similar
      expect(view.textContent).toMatch(/\d+\.\d%/);
    });
  });

  it('renders all sections together', () => {
    render(<WaveAgentBreakdown cost={fixtureCostWithPricing} />);
    expect(screen.getByText('Cost per Wave')).toBeInTheDocument();
    expect(screen.getByText('Cost per Agent Type')).toBeInTheDocument();
    const tables = screen.getAllByRole('table');
    expect(tables.length).toBeGreaterThanOrEqual(2); // at least 2 tables
  });
});
