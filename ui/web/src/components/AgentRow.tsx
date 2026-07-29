/**
 * AgentRow — Expandable agent row with lazy-loaded dispatch prompt.
 *
 * Expansion is keyboard-operable (Enter/Space on button) and scroll-preserving
 * across prop updates (via key-based row identity).
 *
 * Expanded state shows:
 * - Task/status/runtime/tokens + dispatch prompt (fetched from /agent?id=)
 * - Cached with simple eviction (100-item LRU cache per session)
 *
 * D5: all interactive elements are real <button>/<a>, status colors from theme.
 */

import { useState, useRef, useEffect } from 'react';
import type { Agent } from '../lib/types';
import { fetchAgent } from '../lib/api';
import { TESTIDS } from '../test/fixtures';
import './AgentRow.css';

interface AgentRowProps {
  agent: Agent;
  /**
   * Open the read-only Agent Inspector drawer for this agent. When omitted the
   * row simply has no Inspect affordance (keeps standalone/legacy usage intact).
   */
  onInspect?: () => void;
}

// Simple LRU cache for agent details
const agentDetailCache = new Map<string, { data: any; timestamp: number }>();
const MAX_CACHE_SIZE = 100;
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

function getCachedAgentDetail(id: string): any | null {
  const cached = agentDetailCache.get(id);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
    return cached.data;
  }
  if (cached) {
    agentDetailCache.delete(id);
  }
  return null;
}

function setCachedAgentDetail(id: string, data: any) {
  if (agentDetailCache.size >= MAX_CACHE_SIZE) {
    const firstKey = agentDetailCache.keys().next().value;
    if (firstKey !== undefined) {
      agentDetailCache.delete(firstKey);
    }
  }
  agentDetailCache.set(id, { data, timestamp: Date.now() });
}

/**
 * Determine status color.
 */
function getStatusColor(status: string): string {
  if (status === 'running') return 'var(--color-status-info)';
  if (status === 'idle') return 'var(--color-status-neutral)';
  if (status === 'SUSPICIOUS' || status === 'HIGH') return 'var(--color-status-error)';
  if (status === 'MED' || status === 'DRIFT') return 'var(--color-status-warn)';
  return 'var(--color-status-neutral)';
}

export function AgentRow({ agent, onInspect }: AgentRowProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [detail, setDetail] = useState<any | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expandButtonRef = useRef<HTMLButtonElement>(null);

  // Load agent detail on expansion
  useEffect(() => {
    if (!isExpanded) return;

    const cached = getCachedAgentDetail(agent.id);
    if (cached) {
      setDetail(cached);
      return;
    }

    setIsLoading(true);
    setError(null);

    fetchAgent(agent.id)
      .then((data) => {
        setCachedAgentDetail(agent.id, data);
        setDetail(data);
      })
      .catch((err) => {
        setError(err.message || 'Failed to load agent details');
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [isExpanded, agent.id]);

  const handleToggle = () => {
    setIsExpanded(!isExpanded);
  };

  return (
    <li className="agent-row" data-testid={TESTIDS.agentRow} key={agent.id}>
      <div className="agent-row__topline">
        <button
          type="button"
          ref={expandButtonRef}
          className="agent-row__toggle"
          onClick={handleToggle}
          aria-expanded={isExpanded}
          aria-controls={`agent-detail-${agent.id}`}
          aria-label={`${isExpanded ? 'Collapse' : 'Expand'} agent ${agent.id}`}
        >
          <span className="agent-row__toggle-icon">{isExpanded ? '▼' : '▶'}</span>
        </button>

        <span
          className="agent-row__status-icon"
          style={{ color: getStatusColor(agent.status) }}
          aria-label={`Status: ${agent.status}`}
          title={`Status: ${agent.status}`}
        >
          ●
        </span>

        <span className="agent-row__id">{agent.id}</span>

        <span className="agent-row__age">
          {agent.age_s < 60 ? `${agent.age_s}s` : `${Math.floor(agent.age_s / 60)}m`}
        </span>

        {onInspect && (
          <button
            type="button"
            className="agent-row__inspect"
            data-testid={TESTIDS.agentInspectOpen}
            onClick={onInspect}
            aria-label={`Inspect agent ${agent.id}`}
          >
            Inspect
          </button>
        )}
      </div>

      <div className="agent-row__hint">{agent.hint}</div>

      {isExpanded && (
        <div className="agent-row__detail" id={`agent-detail-${agent.id}`} data-testid={TESTIDS.agentRowDetail}>
          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Project:</span>
            <span className="agent-row__detail-value">{agent.project}</span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Status:</span>
            <span
              className="agent-row__detail-value"
              style={{ color: getStatusColor(agent.status) }}
            >
              {agent.status}
            </span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Started:</span>
            <span className="agent-row__detail-value">
              {agent.startedAt ? new Date(agent.startedAt).toLocaleString() : 'unknown'}
            </span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Last Activity:</span>
            <span className="agent-row__detail-value">
              {agent.lastActivity ? new Date(agent.lastActivity).toLocaleString() : 'unknown'}
            </span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Runtime:</span>
            <span className="agent-row__detail-value">
              {typeof agent.runtimeSeconds === 'number'
                ? `${Math.floor(agent.runtimeSeconds / 60)}m ${agent.runtimeSeconds % 60}s`
                : 'unknown'}
            </span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Tokens Used:</span>
            <span className="agent-row__detail-value">
              {typeof agent.tokensUsed === 'number' ? agent.tokensUsed.toLocaleString() : 'unknown'}
            </span>
          </div>

          <div className="agent-row__detail-row">
            <span className="agent-row__detail-label">Task:</span>
            <span className="agent-row__detail-value">{agent.taskLabel}</span>
          </div>

          {isLoading && (
            <div className="agent-row__prompt">
              <span className="agent-row__prompt-label">Dispatch Prompt:</span>
              <div className="agent-row__prompt-loading">Loading...</div>
            </div>
          )}

          {error && (
            <div className="agent-row__prompt">
              <span className="agent-row__prompt-label">Dispatch Prompt:</span>
              <div className="agent-row__prompt-error">Error: {error}</div>
            </div>
          )}

          {detail && !isLoading && (
            <div className="agent-row__prompt">
              <span className="agent-row__prompt-label">Dispatch Prompt:</span>
              <pre className="agent-row__prompt-text">{detail.dispatch_prompt}</pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
