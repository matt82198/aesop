/**
 * QueuePanel — persistent collapsible panel showing merge-queue operator status:
 * - Queue depth (open PRs labeled merge-queue)
 * - Batch state (open merge-queue-batch PRs)
 * - Last advance age (heartbeat mtime, DEGRADED styling if >10min)
 * - Last N exception rows (ts, pr, kind)
 *
 * Default: collapsed (toggle rail only, ~40px). Expanded: ~250px width.
 * Handles three data states: loading, error, empty. Never renders a blank pane.
 *
 * Bound to SSE source (GET /api/queue); no polling.
 */

import { useState, useMemo } from 'react';
import type { QueuePanelData, SSEConnectionStatus } from '../lib/types';
import { TESTIDS } from '../test/fixtures';
import './QueuePanel.css';

interface QueuePanelProps {
  queue: QueuePanelData | null;
  connectionStatus: SSEConnectionStatus;
}

// Empty queue for initialization
const EMPTY_QUEUE: QueuePanelData = {
  queue_depth: 0,
  batch_state: 0,
  last_advance_age: -1,
  last_advance_degraded: false,
  exceptions: [],
};

export function QueuePanel({ queue, connectionStatus }: QueuePanelProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // State determination
  const isLoading = !queue && connectionStatus.status === 'live';
  const isError = !queue && connectionStatus.status !== 'live';
  const isEmpty = queue && queue.queue_depth === 0 && queue.exceptions.length === 0;

  const data = queue ?? EMPTY_QUEUE;

  // Format age as human-readable string
  const formatAge = useMemo(() => {
    if (data.last_advance_age < 0) return 'Unknown';
    if (data.last_advance_age < 60) return `${data.last_advance_age}s`;
    if (data.last_advance_age < 3600) return `${Math.floor(data.last_advance_age / 60)}m`;
    return `${Math.floor(data.last_advance_age / 3600)}h`;
  }, [data.last_advance_age]);

  return (
    <aside
      className="queue-panel"
      data-testid={TESTIDS.queuePanel}
      aria-label="Merge queue operator panel"
    >
      <button
        className="queue-panel__toggle"
        onClick={() => setIsExpanded(!isExpanded)}
        aria-label={isExpanded ? 'Close queue panel' : 'Open queue panel'}
        data-testid={TESTIDS.queuePanelToggle}
        type="button"
      >
        <span className="queue-panel__toggle-icon">🔗</span>
      </button>

      <div
        className="queue-panel__content"
        role="status"
        aria-hidden={!isExpanded}
        data-testid={TESTIDS.queuePanelContent}
      >
        {isLoading && (
          <div className="queue-panel__state" data-testid={TESTIDS.queueLoading}>
            <p className="queue-panel__label">Loading...</p>
            <div className="queue-panel__spinner" />
          </div>
        )}

        {isError && (
          <div className="queue-panel__state queue-panel__state--error" data-testid={TESTIDS.queueError}>
            <p className="queue-panel__label">Error</p>
            {connectionStatus.lastError && (
              <p className="queue-panel__message">{connectionStatus.lastError}</p>
            )}
          </div>
        )}

        {isEmpty && (
          <div className="queue-panel__state" data-testid={TESTIDS.queueEmpty}>
            <p className="queue-panel__label">Queue idle</p>
            <p className="queue-panel__hint">No exceptions - no PRs pending.</p>
          </div>
        )}

        {!isLoading && !isError && !isEmpty && (
          <>
            <div className="queue-panel__metrics">
              <div className="queue-panel__metric" data-testid={TESTIDS.queueDepth}>
                <span className="queue-panel__metric-label">Depth</span>
                <span className="queue-panel__metric-value">{data.queue_depth}</span>
              </div>

              <div className="queue-panel__metric" data-testid={TESTIDS.queueBatchState}>
                <span className="queue-panel__metric-label">Batch</span>
                <span className="queue-panel__metric-value">{data.batch_state}</span>
              </div>

              <div
                className={`queue-panel__metric queue-panel__last-advance${
                  data.last_advance_degraded ? ' queue-panel__last-advance--degraded' : ''
                }`}
                data-testid={TESTIDS.queueLastAdvance}
              >
                <span className="queue-panel__metric-label">Age</span>
                <span className="queue-panel__metric-value">{formatAge}</span>
              </div>
            </div>

            {data.exceptions.length > 0 && (
              <div className="queue-panel__exceptions" data-testid={TESTIDS.queueExceptionsList}>
                <span className="queue-panel__exceptions-label">Exceptions</span>
                <div className="queue-panel__exception-rows">
                  {data.exceptions.map((ex, idx) => (
                    <div key={idx} className="queue-panel__exception-row" data-testid={TESTIDS.queueExceptionRow}>
                      <span className="queue-panel__exception-ts">{formatTimestamp(ex.ts)}</span>
                      <span className="queue-panel__exception-pr">#{ex.pr}</span>
                      <span className="queue-panel__exception-kind">{ex.kind}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

// Helper: format ISO timestamp to HH:MM:SS
function formatTimestamp(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleTimeString('en-US', { hour12: false });
  } catch {
    return iso.split('T')[1]?.substring(0, 8) || iso;
  }
}
