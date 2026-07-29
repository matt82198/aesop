import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { AgentsPanel } from './AgentsPanel';
import { fixtureAgents, TESTIDS } from '../test/fixtures';

vi.mock('./AgentRow', () => ({
  AgentRow: ({ agent }: any) => <div data-testid="agent-row-mock">{agent.id}</div>,
}));

describe('AgentsPanel', () => {
  it('renders agents list with count', () => {
    render(<AgentsPanel agents={fixtureAgents} />);

    expect(screen.getByText(`Fleet Agents (${fixtureAgents.length})`)).toBeInTheDocument();
  });

  it('renders each agent row', () => {
    render(<AgentsPanel agents={fixtureAgents} />);

    const rows = screen.getAllByTestId('agent-row-mock');
    expect(rows).toHaveLength(fixtureAgents.length);
  });

  it('renders all three summary cards with zero counts when no agents', () => {
    render(<AgentsPanel agents={[]} />);

    // Honest empty state: the cards stay visible reading 0, never blank.
    for (const status of ['running', 'idle', 'warnings']) {
      const card = screen.getByTestId(`agents-summary-card-${status}`);
      expect(card).toBeInTheDocument();
      expect(card.textContent).toContain('0');
    }
  });

  it('renders summary cards with zero counts when agents is null', () => {
    render(<AgentsPanel agents={null} />);

    for (const status of ['running', 'idle', 'warnings']) {
      expect(screen.getByTestId(`agents-summary-card-${status}`)).toBeInTheDocument();
    }
  });

  it('has correct data-testid', () => {
    render(<AgentsPanel agents={fixtureAgents} />);

    expect(screen.getByTestId(TESTIDS.agentsPanel)).toBeInTheDocument();
  });

  it('renders status-grouped summary cards with real counts (1 running, 1 idle, 1 warning)', () => {
    render(<AgentsPanel agents={fixtureAgents} />);

    expect(screen.getByTestId(`${TESTIDS.agentsSummaryCard}-running`)).toHaveTextContent('1');
    expect(screen.getByTestId(`${TESTIDS.agentsSummaryCard}-idle`)).toHaveTextContent('1');
    expect(screen.getByTestId(`${TESTIDS.agentsSummaryCard}-warnings`)).toHaveTextContent('1');
  });

  it('collapses a group grid when its summary card is clicked', async () => {
    const user = userEvent.setup();
    render(<AgentsPanel agents={fixtureAgents} />);

    // All groups start expanded
    expect(screen.getByTestId(`${TESTIDS.agentsGroup}-running`)).toBeInTheDocument();

    await user.click(screen.getByTestId(`${TESTIDS.agentsSummaryCard}-running`));

    expect(screen.queryByTestId(`${TESTIDS.agentsGroup}-running`)).not.toBeInTheDocument();
  });

  it('shows an honest empty-state for the warnings group when no agent has issues', () => {
    const healthyAgents = fixtureAgents.filter((a) => a.status === 'running' || a.status === 'idle');
    render(<AgentsPanel agents={healthyAgents} />);

    expect(screen.getByText('Warnings (0) — all agents healthy.')).toBeInTheDocument();
  });
});
