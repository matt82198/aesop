/**
 * Typed API client with CSRF header support.
 * Fetches CSRF token from window.__AESOP_CSRF_TOKEN__ (set via sentinel in index.html)
 * or falls back to GET /api/session if sentinel is unavailable (e.g., dev server without build).
 */

import type {
  FullState,
  CostSummary,
  DashboardData,
  AuditBacklog,
  Agent,
  AgentDetail,
  AgentInspectorDetail,
  TrackerItem,
  AcceptanceCriterion,
  SubmitResponse,
  WavePRBoardData,
  WaveFailureData,
  WaveDispatchData,
  WaveGanttData,
  WaveAuditTailData,
  WaveReasoningTailData,
} from './types';

let _csrfTokenCache: string | null = null;
let _csrfTokenPromise: Promise<string> | null = null;

/**
 * Get the CSRF token from the sentinel-injected global or /api/session endpoint.
 * Caches the result to avoid repeated fetches.
 */
async function getCSRFToken(): Promise<string> {
  // Fast path: check if token is already in the window object
  if (typeof window !== 'undefined') {
    const sentinel = (window as any).__AESOP_CSRF_TOKEN__;
    if (sentinel && typeof sentinel === 'string') {
      return sentinel;
    }
  }

  // Avoid concurrent /api/session calls
  if (_csrfTokenPromise) {
    return _csrfTokenPromise;
  }

  if (_csrfTokenCache) {
    return _csrfTokenCache;
  }

  _csrfTokenPromise = (async () => {
    try {
      const res = await fetch('/api/session', {
        method: 'GET',
        credentials: 'same-origin',
      });
      if (!res.ok) {
        throw new Error(`/api/session returned ${res.status}`);
      }
      const data = await res.json();
      const token = data.token ?? '';
      _csrfTokenCache = token;
      return token;
    } catch (err) {
      console.error('Failed to fetch CSRF token:', err);
      return '';
    }
  })();

  return _csrfTokenPromise;
}

interface FetchOptions extends RequestInit {
  requireCSRF?: boolean;
}

/**
 * Typed fetch wrapper that injects CSRF header for mutations.
 */
async function typedFetch<T>(
  url: string,
  options?: FetchOptions
): Promise<T> {
  const opts = { ...options };

  // For POST/PUT/DELETE, inject CSRF header
  if (opts.requireCSRF !== false && opts.method && ['POST', 'PUT', 'DELETE'].includes(opts.method)) {
    const token = await getCSRFToken();
    opts.headers = {
      ...(opts.headers as Record<string, string>),
      'X-Aesop-Token': token,
      'Content-Type': 'application/json',
    };
  }

  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }

  return res.json() as Promise<T>;
}

/**
 * GET /data — dashboard data snapshot
 */
export async function fetchData(): Promise<DashboardData> {
  return typedFetch('/data', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/state — consolidated first-paint snapshot (U2 adds this)
 */
export async function fetchState(): Promise<FullState> {
  return typedFetch('/api/state', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/backlog — audit backlog
 */
export async function fetchBacklog(): Promise<AuditBacklog> {
  return typedFetch('/api/backlog', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/agents — fleet agents list
 */
export async function fetchAgents(): Promise<Agent[]> {
  return typedFetch('/api/agents', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/tracker — tracker items with optional filters
 */
export async function fetchTrackerItems(
  status?: string,
  priority?: string
): Promise<TrackerItem[]> {
  const params = new URLSearchParams();
  if (status) params.append('status', status);
  if (priority) params.append('priority', priority);
  const query = params.toString();
  const url = `/api/tracker${query ? '?' + query : ''}`;
  return typedFetch(url, { method: 'GET', requireCSRF: false });
}

/**
 * GET /agent?id=<id> — agent dispatch prompt and metadata
 */
export async function fetchAgent(agentId: string): Promise<AgentDetail> {
  const url = `/agent?id=${encodeURIComponent(agentId)}`;
  return typedFetch(url, { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/agent?id=<id> — full agent detail + bounded transcript tail
 * (powers the Agent Inspector drawer). Distinct from fetchAgent() (GET /agent),
 * which returns only the dispatch prompt + metadata.
 */
export async function fetchAgentInspector(agentId: string): Promise<AgentInspectorDetail> {
  const url = `/api/agent?id=${encodeURIComponent(agentId)}`;
  return typedFetch(url, { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/cost — cost summary with token/verdict data (U3)
 */
export async function fetchCost(): Promise<CostSummary> {
  return typedFetch('/api/cost', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/prs — Wave PR board (open PRs + PR-less feat/* branches)
 */
export async function fetchWavePRs(): Promise<WavePRBoardData> {
  return typedFetch('/api/wave/prs', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/failure?pr=N — CI job logs and failure details for a PR
 */
export async function fetchWaveFailure(prNumber: number): Promise<WaveFailureData> {
  const url = `/api/wave/failure?pr=${encodeURIComponent(String(prNumber))}`;
  return typedFetch(url, { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/dispatch — live per-agent phase and activity visibility
 */
export async function fetchWaveDispatch(): Promise<WaveDispatchData> {
  return typedFetch('/api/wave/dispatch', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/gantt — Gantt timeline data for agent phase spans
 */
export async function fetchWaveGantt(): Promise<WaveGanttData> {
  return typedFetch('/api/wave/gantt', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/audit-tail — latest audit/verification outcomes
 */
export async function fetchWaveAuditTail(): Promise<WaveAuditTailData> {
  return typedFetch('/api/wave/audit-tail', { method: 'GET', requireCSRF: false });
}

/**
 * GET /api/wave/reasoning-tail — per-agent live reasoning transparency
 */
export async function fetchWaveReasoningTail(): Promise<WaveReasoningTailData> {
  return typedFetch('/api/wave/reasoning-tail', { method: 'GET', requireCSRF: false });
}

/**
 * Generic GET helper for typed API calls (wave telemetry, etc.)
 */
export async function fetchApi<T>(url: string): Promise<T> {
  return typedFetch<T>(url, { method: 'GET', requireCSRF: false });
}

/**
 * POST /submit — submit text to inbox
 */
export async function submitInbox(text: string): Promise<SubmitResponse> {
  return typedFetch('/submit', {
    method: 'POST',
    requireCSRF: true,
    body: JSON.stringify({ text }),
  });
}

/**
 * POST /api/tracker — create tracker item
 */
export async function createTrackerItem(data: {
  title: string;
  priority?: string;
  status?: string;
  lane?: string;
  source?: string;
  tags?: string[];
  notes?: string;
  pr_link?: string;
  acceptanceCriteria?: AcceptanceCriterion[];
}): Promise<TrackerItem> {
  return typedFetch('/api/tracker', {
    method: 'POST',
    requireCSRF: true,
    body: JSON.stringify(data),
  });
}

/**
 * POST /api/tracker/<id> — update tracker item
 */
export async function updateTrackerItem(
  itemId: string,
  data: Partial<{
    status: string;
    lane: string;
    priority: string;
    notes: string;
    pr_link: string;
    tags: string[];
    acceptanceCriteria: AcceptanceCriterion[];
  }>
): Promise<TrackerItem> {
  const url = `/api/tracker/${encodeURIComponent(itemId)}`;
  return typedFetch(url, {
    method: 'POST',
    requireCSRF: true,
    body: JSON.stringify(data),
  });
}

/**
 * POST /api/tracker/<id>?action=delete — delete (archive) tracker item
 */
export async function deleteTrackerItem(itemId: string): Promise<TrackerItem> {
  const url = `/api/tracker/${encodeURIComponent(itemId)}?action=delete`;
  return typedFetch(url, {
    method: 'POST',
    requireCSRF: true,
    body: '{}',
  });
}
