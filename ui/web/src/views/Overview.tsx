/**
 * Overview view — main dashboard combining all overview components.
 *
 * Mission-Control 2-column layout:
 * - Main (primary, ~70%): Wave Progress, Fleet Agents, Quick Inbox — the
 *   "what is the fleet doing right now" answer, front and center.
 * - Sidebar (secondary, ~30%, sticky): Alerts, Events, Repos — supporting
 *   context, scrollable independently so it never pushes the main column down.
 *
 * Collapses to a single stacked column at <=1024px (sidebar moves below main).
 *
 * Props passed from App.tsx via SSE state.
 */

import type { Agent, Alert } from '../lib/types';
import { AgentsPanel } from '../components/AgentsPanel';
import { AlertsPanel } from '../components/AlertsPanel';
import { EventsFeed } from '../components/EventsFeed';
import { ReposPanel } from '../components/ReposPanel';
import { InboxForm } from '../components/InboxForm';
import { WaveTelemetryProgress } from '../components/WaveTelemetryProgress';
import { TESTIDS } from '../test/fixtures';
import './Overview.css';

interface OverviewProps {
  agents: Agent[] | null;
  alerts: Alert | null;
  events: string[] | null;
  repos: any[] | null;
}

export function Overview({ agents, alerts, events, repos }: OverviewProps) {
  return (
    <div className="overview" data-testid={TESTIDS.viewOverview}>
      <div className="overview__main" data-testid={TESTIDS.overviewMain}>
        <section className="overview__section overview__section--full">
          <WaveTelemetryProgress />
        </section>

        <section className="overview__section overview__section--full">
          <AgentsPanel agents={agents} />
        </section>

        <section className="overview__section overview__section--full">
          <InboxForm />
        </section>
      </div>

      <div className="overview__sidebar" data-testid={TESTIDS.overviewSidebar}>
        <AlertsPanel alerts={alerts} />
        <EventsFeed events={events} />
        <ReposPanel repos={repos} />
      </div>
    </div>
  );
}
