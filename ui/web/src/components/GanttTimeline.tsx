/**
 * GanttTimeline — Per-wave agent Gantt chart visualization.
 * Shows agents as rows with phase spans as bars, with timing info from ledger/journal.
 * Integrated into the Activity view for deep execution visibility.
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import { fetchWaveGantt } from '../lib/api';
import { formatTimestamp } from '../lib/format';
import { TESTIDS } from '../test/fixtures';
import styles from './GanttTimeline.module.css';

export interface GanttAgent {
  id: string;
  phases: Array<{
    phase: string;
    start: string;
    end: string;
    duration_sec: number | null;
    token_estimate?: number;
  }>;
  total_duration_sec: number | null;
  status: 'running' | 'done' | 'stalled' | 'inactive';
}

export interface GanttData {
  available: boolean;
  wave_phase?: string;
  agents: GanttAgent[];
  at: string;
  error?: string;
}

interface GanttTimelineProps {
  containerRef?: React.RefObject<HTMLDivElement>;
  fetcher?: () => Promise<GanttData>;
}

const POLL_INTERVAL_MS = 3000; // 3 seconds
const VISIBLE_CHECK_INTERVAL_MS = 500;

// Phase to color mapping
const PHASE_COLORS: Record<string, string> = {
  dispatch: '#3b82f6',     // blue
  thinking: '#06b6d4',     // cyan
  'tool-use': '#10b981',   // green
  'tool_use': '#10b981',   // green (underscore variant)
  stall: '#f59e0b',        // amber
  done: '#6b7280',         // gray
};

function getPhaseColor(phase: string): string {
  return PHASE_COLORS[phase.toLowerCase()] || '#9ca3af'; // default gray
}

function formatDuration(seconds: number | null | undefined): string {
  // null/undefined = duration unknown (server emits None). Never coerce it to a number.
  if (seconds == null || Number.isNaN(seconds)) return 'unknown';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function calculateBarPosition(
  phaseStart: string,
  phaseEnd: string,
  timelineStart: string,
  timelineEnd: string
): { left: number; width: number } {
  try {
    const start = new Date(phaseStart.replace('Z', '+00:00')).getTime();
    const end = new Date(phaseEnd.replace('Z', '+00:00')).getTime();
    const tlStart = new Date(timelineStart.replace('Z', '+00:00')).getTime();
    const tlEnd = new Date(timelineEnd.replace('Z', '+00:00')).getTime();

    if (tlEnd <= tlStart) return { left: 0, width: 5 };

    const tlDuration = tlEnd - tlStart;
    const left = Math.max(0, (start - tlStart) / tlDuration * 100);
    const width = Math.max(3, (end - start) / tlDuration * 100);

    return { left: Math.min(100, left), width: Math.min(100 - left, width) };
  } catch (err) {
    return { left: 0, width: 5 };
  }
}

export default function GanttTimeline({
  containerRef,
  fetcher = fetchWaveGantt,
}: GanttTimelineProps) {
  const [gantt, setGantt] = useState<GanttData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isVisible, setIsVisible] = useState(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const visibilityCheckTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchGantt = useCallback(async () => {
    try {
      const data = await fetcher();
      setGantt(data);
      setError(null);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMsg);
      console.error('[GanttTimeline] fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  const checkVisibility = useCallback(() => {
    if (!containerRef?.current) {
      setIsVisible(true);
      return;
    }
    const rect = containerRef.current.getBoundingClientRect();
    const visible = rect.bottom > 0 && rect.top < window.innerHeight;
    setIsVisible(visible);
  }, [containerRef]);

  useEffect(() => {
    if (isVisible && !pollTimerRef.current) {
      fetchGantt();
      pollTimerRef.current = setInterval(fetchGantt, POLL_INTERVAL_MS);
    } else if (!isVisible && pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [isVisible, fetchGantt]);

  useEffect(() => {
    checkVisibility();
    visibilityCheckTimerRef.current = setInterval(checkVisibility, VISIBLE_CHECK_INTERVAL_MS);
    return () => {
      if (visibilityCheckTimerRef.current) {
        clearInterval(visibilityCheckTimerRef.current);
        visibilityCheckTimerRef.current = null;
      }
    };
  }, [checkVisibility]);

  if (!gantt && loading) {
    return (
      <div data-testid={TESTIDS.ganttTimeline} className={styles.container}>
        <div className={styles.header}>
          <h3 className={styles.title}>Wave Gantt Timeline</h3>
          <div className={styles.status}>Loading...</div>
        </div>
      </div>
    );
  }

  if (error || !gantt) {
    return (
      <div data-testid={TESTIDS.ganttTimeline} className={styles.container}>
        <div className={styles.header}>
          <h3 className={styles.title}>Wave Gantt Timeline</h3>
        </div>
        <div className={styles.unavailable}>
          {error || 'No active workflow'}
        </div>
      </div>
    );
  }

  if (!gantt.available) {
    return (
      <div data-testid={TESTIDS.ganttTimeline} className={styles.container}>
        <div className={styles.header}>
          <h3 className={styles.title}>Wave Gantt Timeline</h3>
        </div>
        <div className={styles.unavailable}>
          No active workflow
        </div>
      </div>
    );
  }

  // Calculate timeline bounds from all agents
  let earliestStart: string | null = null;
  let latestEnd: string | null = null;

  gantt.agents.forEach((agent) => {
    agent.phases.forEach((phase) => {
      if (!earliestStart || phase.start < earliestStart) {
        earliestStart = phase.start;
      }
      if (!latestEnd || phase.end > latestEnd) {
        latestEnd = phase.end;
      }
    });
  });

  // Fallback to now if we have no timeline data
  const now = new Date().toISOString();
  const tlStart = earliestStart || now;
  const tlEnd = latestEnd || now;

  return (
    <div data-testid={TESTIDS.ganttTimeline} className={styles.container}>
      <div className={styles.header}>
        <h3 className={styles.title}>Wave Gantt Timeline</h3>
        {gantt.wave_phase && <div className={styles.phase}>{gantt.wave_phase}</div>}
        <div className={styles.timestamp}>{new Date(gantt.at).toLocaleTimeString()}</div>
      </div>

      <div className={styles.ganttContent}>
        {gantt.agents.length === 0 ? (
          <div className={styles.empty}>No agents in this wave</div>
        ) : (
          <div className={styles.ganttTable}>
            {/* Header row with time labels */}
            <div className={styles.headerRow}>
              <div className={styles.agentLabel}>Agent</div>
              <div className={styles.timelineHeader}>
                <div className={styles.timeLabel}>{formatTimestamp(tlStart)}</div>
                <div className={styles.timeLabel} style={{ position: 'absolute', right: '10px' }}>
                  {formatTimestamp(tlEnd)}
                </div>
              </div>
            </div>

            {/* Agent rows */}
            {gantt.agents.map((agent) => (
              <div key={agent.id} data-testid={TESTIDS.ganttRow} className={styles.agentRow}>
                <div className={styles.agentLabel}>
                  <span className={styles.agentId}>{agent.id}</span>
                  <span className={styles.agentStatus}>{agent.status}</span>
                </div>
                <div className={styles.ganttBar}>
                  {agent.phases.map((phase, idx) => {
                    const { left, width } = calculateBarPosition(
                      phase.start,
                      phase.end,
                      tlStart,
                      tlEnd
                    );
                    return (
                      <div
                        key={idx}
                        data-testid={TESTIDS.ganttPhaseBar}
                        className={styles.phaseBar}
                        style={{
                          left: `${left}%`,
                          width: `${width}%`,
                          backgroundColor: getPhaseColor(phase.phase),
                        }}
                        title={`${phase.phase}: ${formatDuration(phase.duration_sec)}${
                          phase.token_estimate ? ` (~${phase.token_estimate}T)` : ''
                        }`}
                        aria-label={`Phase ${phase.phase} (${formatDuration(phase.duration_sec)})`}
                      >
                        {width > 15 && (
                          <span className={styles.phaseLabel}>{phase.phase}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className={styles.agentDuration}>{formatDuration(agent.total_duration_sec)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className={styles.legend}>
        <span className={styles.legendItem}>
          <span className={styles.legendColor} style={{ backgroundColor: '#3b82f6' }} /> Dispatch
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendColor} style={{ backgroundColor: '#06b6d4' }} /> Thinking
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendColor} style={{ backgroundColor: '#10b981' }} /> Tool Use
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendColor} style={{ backgroundColor: '#f59e0b' }} /> Stall
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendColor} style={{ backgroundColor: '#6b7280' }} /> Done
        </span>
      </div>
    </div>
  );
}
