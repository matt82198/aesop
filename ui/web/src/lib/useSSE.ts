/**
 * useSSE hook — EventSource wrapper with reconnect backoff, per-section state, and connection status.
 *
 * Usage:
 *   const { data, backlog, agents, tracker, status, cost, connectionStatus } = useSSE();
 *
 * Emits on sections: data, backlog, agents, tracker, status, cost
 * Handles connection errors and automatic reconnect with exponential backoff (1s → 10s max).
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type { DashboardData, AuditBacklog, Agent, TrackerSnapshot, OrchestratorStatus, CostSummary, SSEConnectionStatus } from './types';

/**
 * Extended connection status with attempt tracking and retry timing.
 * Includes attemptNumber and nextRetryMs for better UX during reconnection.
 */
export interface ExtendedSSEConnectionStatus extends SSEConnectionStatus {
  attemptNumber?: number;
  nextRetryMs?: number;
}

export interface SSEState {
  data: DashboardData | null;
  backlog: AuditBacklog | null;
  agents: Agent[] | null;
  tracker: TrackerSnapshot | null;
  status: OrchestratorStatus | null;
  cost: CostSummary | null;
  connectionStatus: ExtendedSSEConnectionStatus;
  lastHeartbeat: number | null; // Epoch ms of last heartbeat event from collector
}

const initialState: SSEState = {
  data: null,
  backlog: null,
  agents: null,
  tracker: null,
  status: null,
  cost: null,
  connectionStatus: { status: 'reconnecting' },
  lastHeartbeat: null,
};

export function useSSE() {
  const [state, setState] = useState<SSEState>(initialState);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const getReconnectDelay = useCallback(() => {
    const baseDelay = 1000; // 1 second
    const maxDelay = 10000; // 10 seconds
    const exponentialDelay = Math.min(baseDelay * Math.pow(2, reconnectAttemptRef.current), maxDelay);
    // Add jitter: ±10% to avoid thundering herd
    const jitterFactor = 0.9 + Math.random() * 0.2; // Range: [0.9, 1.1]
    return exponentialDelay * jitterFactor;
  }, []);

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      return; // Already connected or connecting
    }

    const eventSource = new EventSource('/events');

    eventSource.addEventListener('data', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          data: payload,
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE data:', err);
      }
    });

    eventSource.addEventListener('backlog', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          backlog: payload,
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE backlog:', err);
      }
    });

    eventSource.addEventListener('agents', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          agents: Array.isArray(payload) ? payload : [],
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE agents:', err);
      }
    });

    eventSource.addEventListener('tracker', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          tracker: payload,
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE tracker:', err);
      }
    });

    eventSource.addEventListener('status', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          status: payload,
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE status:', err);
      }
    });

    eventSource.addEventListener('cost', (e) => {
      try {
        const payload = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          cost: payload,
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE cost:', err);
      }
    });

    eventSource.addEventListener('heartbeat', (e) => {
      try {
        JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          lastHeartbeat: Date.now(),
          connectionStatus: { status: 'live' },
        }));
        reconnectAttemptRef.current = 0;
      } catch (err) {
        console.error('Failed to parse SSE heartbeat:', err);
      }
    });

    eventSource.addEventListener('error', (err) => {
      // Extract error message from the event
      const errorMessage = err instanceof Event && 'message' in err
        ? (err as any).message
        : err instanceof ErrorEvent
          ? err.message
          : 'Connection lost';

      console.error(
        `EventSource error (attempt ${reconnectAttemptRef.current + 1}): ${errorMessage}`,
        err
      );

      eventSource.close();
      eventSourceRef.current = null;
      reconnectAttemptRef.current += 1;
      const delay = getReconnectDelay();

      setState((prev) => ({
        ...prev,
        connectionStatus: {
          status: 'reconnecting',
          lastError: `Reconnecting (attempt ${reconnectAttemptRef.current})`,
          attemptNumber: reconnectAttemptRef.current,
          nextRetryMs: delay,
        },
      }));

      reconnectTimeoutRef.current = setTimeout(connect, delay);
    });

    eventSourceRef.current = eventSource;
    setState((prev) => ({
      ...prev,
      connectionStatus: { status: 'reconnecting' },
    }));
  }, [getReconnectDelay]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [connect]);

  return state;
}
