/**
 * DispatchPanel — Live per-agent phase and activity visibility.
 * Polls /api/wave/dispatch every 2-3s to show real-time worker status.
 * Displays per-agent row with phase badge, activity age, and token burn.
 * Embedded in the Activity view.
 *
 * Takes an optional `fetcher` prop for dependency injection in tests.
 * If not provided, uses the real fetchWaveDispatch from ../lib/api.
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import { fetchWaveDispatch as defaultFetcher } from '../lib/api';
import type { WaveDispatchData } from '../lib/types';
import { TESTIDS } from '../test/fixtures';
import styles from './DispatchPanel.module.css';

const POLL_INTERVAL_MS = 2500; // 2.5 seconds
const VISIBLE_CHECK_INTERVAL_MS = 500; // Check visibility every 500ms

interface DispatchPanelProps {
  // Optional container ref for visibility detection
  containerRef?: React.RefObject<HTMLDivElement>;
  // Optional fetcher for dependency injection in tests
  fetcher?: () => Promise<WaveDispatchData>;
}

export default function DispatchPanel({ containerRef, fetcher = defaultFetcher }: DispatchPanelProps) {
  const [dispatch, setDispatch] = useState<WaveDispatchData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isVisible, setIsVisible] = useState(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const visibilityCheckTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch dispatch data
  const fetchDispatch = useCallback(async () => {
    try {
      const data = await fetcher();
      setDispatch(data);
      setError(null);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMsg);
      console.error('[DispatchPanel] fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  // Check if component is visible in viewport
  const checkVisibility = useCallback(() => {
    if (!containerRef?.current) {
      setIsVisible(true);
      return;
    }

    const rect = containerRef.current.getBoundingClientRect();
    // Visible if any part of the element is in the viewport
    const visible = rect.bottom > 0 && rect.top < window.innerHeight;
    setIsVisible(visible);
  }, [containerRef]);

  // Set up polling: only poll when visible
  useEffect(() => {
    if (isVisible && !pollTimerRef.current) {
      // Fetch immediately
      fetchDispatch();
      // Set up polling
      pollTimerRef.current = setInterval(fetchDispatch, POLL_INTERVAL_MS);
    } else if (!isVisible && pollTimerRef.current) {
      // Stop polling when not visible
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [isVisible, fetchDispatch]);

  // Set up visibility check
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

  // Format age for display
  const formatAge = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h`;
  };

  // Format tokens for display
  const formatTokens = (tokens: number): string => {
    if (tokens < 1000) return `${tokens}`;
    if (tokens < 1000000) return `${(tokens / 1000).toFixed(1)}K`;
    return `${(tokens / 1000000).toFixed(1)}M`;
  };

  // Determine phase badge color
  const getPhaseColor = (phase: string): string => {
    switch (phase.toLowerCase()) {
      case 'dispatch':
        return 'blue';
      case 'thinking':
        return 'cyan';
      case 'tool-use':
        return 'green';
      case 'stall':
        return 'orange';
      case 'done':
        return 'gray';
      default:
        return 'gray';
    }
  };

  if (!dispatch && loading) {
    return (
      <div data-testid={TESTIDS.dispatchPanel} className={styles.container}>
        <div className={styles.header}>
          <h4 className={styles.title}>Wave Dispatch</h4>
          <div className={styles.timestamp}>Loading...</div>
        </div>
      </div>
    );
  }

  if (error || !dispatch) {
    return (
      <div data-testid={TESTIDS.dispatchPanelUnavailable} className={styles.container}>
        <div className={styles.header}>
          <h4 className={styles.title}>Wave Dispatch</h4>
        </div>
        <div className={styles.unavailable}>
          No active workflow
        </div>
      </div>
    );
  }

  if (!dispatch.available) {
    return (
      <div data-testid={TESTIDS.dispatchPanelUnavailable} className={styles.container}>
        <div className={styles.header}>
          <h4 className={styles.title}>Wave Dispatch</h4>
        </div>
        <div className={styles.unavailable}>
          No active workflow
        </div>
      </div>
    );
  }

  return (
    <div data-testid={TESTIDS.dispatchPanel} className={styles.container}>
      <div className={styles.header}>
        <h4 className={styles.title}>Wave Dispatch</h4>
        {dispatch.wave_phase && <div className={styles.phase}>{dispatch.wave_phase}</div>}
        <div className={styles.timestamp}>{new Date(dispatch.at).toLocaleTimeString()}</div>
      </div>

      <div className={styles.agentsList}>
        {dispatch.agents.length === 0 ? (
          <div className={styles.empty}>No agents currently active</div>
        ) : (
          dispatch.agents.map((agent) => (
            <div key={agent.id} data-testid={TESTIDS.dispatchAgentRow} className={styles.agentRow}>
              <div className={styles.agentId}>{agent.id}</div>
              <div className={styles.agentMetrics}>
                <div
                  data-testid={TESTIDS.dispatchAgentPhase}
                  className={`${styles.phaseBadge} ${styles[`phase-${getPhaseColor(agent.phase)}`]}`}
                >
                  {agent.phase}
                </div>
                <div data-testid={TESTIDS.dispatchAgentAge} className={styles.age}>
                  {formatAge(agent.last_activity_age_sec)}
                </div>
                <div data-testid={TESTIDS.dispatchAgentTokens} className={styles.tokens}>
                  {formatTokens(agent.token_estimate)}T
                </div>
              </div>
              {agent.warnings && agent.warnings.length > 0 && (
                <div className={styles.warnings}>{agent.warnings.join(', ')}</div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
