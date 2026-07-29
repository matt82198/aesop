/**
 * AgentsPanel — Fleet agents, status-grouped into Running / Idle / Warnings.
 *
 * Top-level view shows one summary card per status group (real counts derived
 * from the live `agents` array — never invented). Clicking a summary card
 * toggles a responsive card grid of that group's agents (each an expandable
 * AgentRow with its existing dispatch-prompt drill-down).
 */

import { useState } from 'react';
import type { Agent } from '../lib/types';
import { AgentRow } from './AgentRow';
import { AgentInspector } from './AgentInspector';
import { TESTIDS } from '../test/fixtures';
import './AgentsPanel.css';

type StatusGroup = 'running' | 'idle' | 'warnings';

const GROUP_ORDER: StatusGroup[] = ['running', 'idle', 'warnings'];
const GROUP_LABEL: Record<StatusGroup, string> = {
  running: 'Running',
  idle: 'Idle',
  warnings: 'Warnings',
};

/** Bucket a raw agent status into one of the three headline groups. */
function bucketOf(status: string): StatusGroup {
  if (status === 'running') return 'running';
  if (status === 'idle') return 'idle';
  return 'warnings';
}

function groupAgents(agents: Agent[]): Record<StatusGroup, Agent[]> {
  const groups: Record<StatusGroup, Agent[]> = { running: [], idle: [], warnings: [] };
  agents.forEach((agent) => {
    groups[bucketOf(agent.status)].push(agent);
  });
  return groups;
}

interface AgentsPanelProps {
  agents: Agent[] | null;
}

export function AgentsPanel({ agents }: AgentsPanelProps) {
  // Which agent's read-only Inspector drawer is open (by id), if any.
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  // Groups start expanded so the fleet is fully visible on first paint;
  // clicking a summary card toggles that group's grid.
  const [expanded, setExpanded] = useState<Record<StatusGroup, boolean>>({
    running: true,
    idle: true,
    warnings: true,
  });

  // No early return on an empty fleet: the three status summary cards always
  // render (with honest zero counts) so the panel reads as "0 running" rather
  // than disappearing — observability over blankness.
  const groups = groupAgents(agents ?? []);
  const inspectedAgent = (agents ?? []).find((a) => a.id === inspectedId) ?? null;

  const toggleGroup = (group: StatusGroup) => {
    setExpanded((prev) => ({ ...prev, [group]: !prev[group] }));
  };

  return (
    <section className="agents-panel" data-testid={TESTIDS.agentsPanel}>
      <h2>Fleet Agents ({(agents ?? []).length})</h2>

      <div className="agents-panel__summary" role="list" aria-label="Agent status groups">
        {GROUP_ORDER.map((group) => (
          <button
            type="button"
            key={group}
            className="agents-summary-card"
            data-status={group}
            data-testid={`${TESTIDS.agentsSummaryCard}-${group}`}
            aria-expanded={expanded[group]}
            aria-label={`${GROUP_LABEL[group]}: ${groups[group].length} agents`}
            onClick={() => toggleGroup(group)}
          >
            <span className="agents-summary-card__count">{groups[group].length}</span>
            <span className="agents-summary-card__label">{GROUP_LABEL[group]}</span>
          </button>
        ))}
      </div>

      {GROUP_ORDER.map((group) => {
        const groupAgentsList = groups[group];
        if (groupAgentsList.length === 0) {
          return group === 'warnings' ? (
            <p key={group} className="agents-panel__group-empty" data-testid={`${TESTIDS.agentsGroup}-${group}-empty`}>
              Warnings (0) — all agents healthy.
            </p>
          ) : null;
        }
        if (!expanded[group]) return null;
        return (
          <div key={group} className="agents-panel__group" data-testid={`${TESTIDS.agentsGroup}-${group}`}>
            <h3 className="agents-panel__group-title">
              {GROUP_LABEL[group]} ({groupAgentsList.length})
            </h3>
            <ul className="agents-panel__grid">
              {groupAgentsList.map((agent) => (
                <AgentRow key={agent.id} agent={agent} onInspect={() => setInspectedId(agent.id)} />
              ))}
            </ul>
          </div>
        );
      })}

      {inspectedAgent && (
        <AgentInspector agent={inspectedAgent} onClose={() => setInspectedId(null)} />
      )}
    </section>
  );
}
