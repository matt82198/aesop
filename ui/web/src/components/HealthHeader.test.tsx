import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { HealthHeader } from './HealthHeader';
import {
  fixtureWatchdog,
  fixtureWatchdogStale,
  fixtureMonitor,
  fixtureAlerts,
  fixtureAlertsEmpty,
  fixtureAgents,
  fixtureAgentsNoIssues,
  fixtureStatus,
  fixtureCost,
  fixtureCostWithPricing,
  TESTIDS,
} from '../test/fixtures';

describe('HealthHeader', () => {
  const mockThemeToggle = vi.fn();
  const mockRefresh = vi.fn();

  it('renders all health cells', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlerts}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    expect(screen.getByTestId(TESTIDS.healthHeader)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthWatchdog)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthMonitor)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthOrchestrator)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthAgentsCount)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthAlertsCount)).toBeInTheDocument();
  });

  it('displays watchdog ALIVE status', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const watchdogCell = screen.getByTestId(TESTIDS.healthWatchdog);
    expect(watchdogCell).toHaveTextContent('ALIVE');
  });

  it('displays watchdog STALE status with age', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdogStale}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const watchdogCell = screen.getByTestId(TESTIDS.healthWatchdog);
    expect(watchdogCell).toHaveTextContent('STALE');
    expect(watchdogCell).toHaveTextContent('10m');
  });

  it('displays monitor status', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const monitorCell = screen.getByTestId(TESTIDS.healthMonitor);
    expect(monitorCell).toHaveTextContent('ALIVE');
  });

  it('displays agent count', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const agentsCell = screen.getByTestId(TESTIDS.healthAgentsCount);
    expect(agentsCell).toHaveTextContent('3');
  });

  it('displays alerts count', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlerts}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const alertsCell = screen.getByTestId(TESTIDS.healthAlertsCount);
    expect(alertsCell).toHaveTextContent('2');
  });

  it('displays SSE live status', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const sseCell = screen.getByTestId(TESTIDS.sseStatus);
    expect(sseCell).toHaveTextContent('Live');
  });

  it('displays SSE reconnecting status', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'reconnecting' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const sseCell = screen.getByTestId(TESTIDS.sseStatus);
    expect(sseCell).toHaveTextContent('Reconnecting');
  });

  it('calls onThemeToggle when theme button clicked', async () => {
    const user = userEvent.setup();
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const themeButton = screen.getByTestId(TESTIDS.themeToggle);
    await user.click(themeButton);
    expect(mockThemeToggle).toHaveBeenCalled();
  });

  it('calls onRefresh when refresh button clicked', async () => {
    const user = userEvent.setup();
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const refreshButton = screen.getByTestId(TESTIDS.refreshButton);
    await user.click(refreshButton);
    expect(mockRefresh).toHaveBeenCalled();
  });

  it('is keyboard accessible (buttons can receive focus)', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const themeButton = screen.getByTestId(TESTIDS.themeToggle);
    const refreshButton = screen.getByTestId(TESTIDS.refreshButton);

    expect(themeButton).toHaveAttribute('type', 'button');
    expect(refreshButton).toHaveAttribute('type', 'button');
  });

  it('handles null values gracefully', () => {
    render(
      <HealthHeader
        watchdog={null}
        monitor={null}
        orchestrator={null}
        agents={null}
        alerts={null}
        connectionStatus={{ status: 'error' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    expect(screen.getByTestId(TESTIDS.healthHeader)).toBeInTheDocument();
    expect(screen.getByTestId(TESTIDS.healthWatchdog)).toHaveTextContent('unknown');
  });

  it('displays audit phase badge when orchestrator is in audit phase', () => {
    const auditStatus = {
      orchestrators: [
        {
          id: 'main',
          role: 'orchestrator',
          activity: 'running audit',
          phase: 'audit',
          age_seconds: 42,
          stale: false,
        },
      ],
    };

    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={auditStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const orchestratorCell = screen.getByTestId(TESTIDS.healthOrchestrator);
    expect(orchestratorCell).toHaveTextContent('Audit');
  });

  it('renders 3 status-first zones (fleet, system, controls)', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    expect(screen.getByTestId(`${TESTIDS.healthZone}-fleet`)).toBeInTheDocument();
    expect(screen.getByTestId(`${TESTIDS.healthZone}-system`)).toBeInTheDocument();
    expect(screen.getByTestId(`${TESTIDS.healthZone}-controls`)).toBeInTheDocument();
  });

  it('breaks down agent status badges from the live agents array (1 running, 1 idle, 1 issue)', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    expect(screen.getByTestId(TESTIDS.healthAgentsRunning)).toHaveTextContent('1');
    expect(screen.getByTestId(TESTIDS.healthAgentsIdle)).toHaveTextContent('1');
    expect(screen.getByTestId(TESTIDS.healthAgentsIssues)).toHaveTextContent('1');
  });

  it('renders the Warnings badge in its success/green state when there are zero warnings', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgentsNoIssues}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const issuesBadge = screen.getByTestId(TESTIDS.healthAgentsIssues);
    expect(issuesBadge).toHaveTextContent('0');
    expect(issuesBadge).toHaveAttribute('data-empty', 'true');
  });

  it('renders the Warnings badge in its normal/error state when there are warnings', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    const issuesBadge = screen.getByTestId(TESTIDS.healthAgentsIssues);
    expect(issuesBadge).toHaveTextContent('1');
    expect(issuesBadge).toHaveAttribute('data-empty', 'false');
  });

  it('shows an honest empty-state for cost when pricing is not configured', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        cost={fixtureCost}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    expect(screen.getByTestId(TESTIDS.healthCost)).toHaveTextContent('n/a');
  });

  it('shows a real dollar cost snapshot when pricing is configured', () => {
    render(
      <HealthHeader
        watchdog={fixtureWatchdog}
        monitor={fixtureMonitor}
        orchestrator={fixtureStatus}
        agents={fixtureAgents}
        alerts={fixtureAlertsEmpty}
        cost={fixtureCostWithPricing}
        connectionStatus={{ status: 'live' }}
        onThemeToggle={mockThemeToggle}
        onRefresh={mockRefresh}
      />
    );

    // 4.19 + 5.83 = 10.02 (sum of estimates_by_model.total_cost)
    expect(screen.getByTestId(TESTIDS.healthCost)).toHaveTextContent('$10.02');
  });
});
