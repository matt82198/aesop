/**
 * TypeScript types for Aesop UI API contracts.
 * These types are imported by U4–U7 components and must remain stable across the wave.
 */

export interface HeartbeatStatus {
  alive: 'ALIVE' | 'STALE' | 'UNKNOWN' | 'unknown' | 'not running';
  age: number; // seconds, bucketed to 3-second intervals; -1 if unknown
  threshold: number; // seconds until considered stale
}

/**
 * One fleet agent — the JSON emitted by dash/dash-extra.mjs --json
 * (served via GET /api/agents and the `agents` SSE section; also embedded
 * as `agents` inside GET /data).
 * `status`: 'running' | 'idle' age-derived, overridden by security-log
 * severities 'SUSPICIOUS' | 'HIGH' | 'DRIFT' | 'MED'.
 * ui/agents.py de-dupes colliding 13-char ids by suffixing "-2", "-3", ...
 */
export interface Agent {
  id: string;
  project: string;
  status: 'running' | 'idle' | 'SUSPICIOUS' | 'HIGH' | 'DRIFT' | 'MED' | string;
  age_s: number; // seconds since transcript mtime
  hint: string; // label, capped at 60 chars
  startedAt: string | null; // ISO 8601 transcript timestamp
  lastActivity: string | null; // ISO 8601 transcript timestamp
  runtimeSeconds?: number;
  tokensUsed?: number;
  taskLabel: string; // first prompt line, capped at 80 chars
  promptFull?: string;
}

/**
 * GET /agent?id=<id> — dispatch prompt + metadata
 * (ui/agents.py extract_agent_dispatch_prompt).
 * Error responses are {error: string} with 400 (invalid id) or 404 (no transcript).
 */
export interface AgentDetail {
  id: string;
  dispatch_prompt: string;
  dispatcher: 'main thread' | 'parent agent';
  model: string; // "unknown" when not found in transcript
  message_count: number;
  first_seen: number; // epoch seconds (file mtime)
  last_activity: number; // epoch seconds (file mtime)
}

/**
 * One rendered line of an agent transcript tail (GET /api/agent?id=).
 * `text` is a PLAIN string (backend extracts + secret-redacts it); the client
 * renders it as escaped text, never as HTML.
 */
export interface TranscriptTailEntry {
  type: string; // 'user' | 'assistant' | 'system' | 'tool_result' | 'raw' | ...
  text: string;
}

/**
 * GET /api/agent?id=<id> — full agent detail for the Inspector drawer
 * (ui/agents.py get_agent_detail). Superset of AgentDetail: adds the bounded,
 * secret-redacted transcript tail. Error responses are {error: string} with
 * 400 (invalid id) or 404 (no transcript), same as GET /agent.
 */
export interface AgentInspectorDetail {
  id: string;
  dispatch_prompt: string;
  dispatcher: 'main thread' | 'parent agent';
  model: string; // "unknown" when not found in transcript
  message_count: number;
  first_seen: number; // epoch seconds (file mtime)
  last_activity: number; // epoch seconds (file mtime)
  transcript_tail: TranscriptTailEntry[];
  tail_truncated: boolean; // true if the transcript was longer than the tail window
}

/** GET /api/session — CSRF token for same-origin JS (U2 adds this). */
export interface SessionResponse {
  token: string;
}

/** Uniform error payload shape for non-2xx JSON responses. */
export interface ApiError {
  error: string;
}

export interface Alert {
  count: number;
  lines: string[];
}

export interface AcceptanceCriterion {
  statement: string;
  verifiable_by: string;
}

export interface TrackerItem {
  id: string;
  title: string;
  priority: 'P0' | 'P1' | 'P2';
  status: 'todo' | 'done' | 'in-progress' | 'archived';
  lane: 'proposed' | 'ranked' | 'in-progress' | 'done';
  source: string;
  tags: string[];
  notes: string | null;
  pr_link: string | null;
  acceptanceCriteria?: AcceptanceCriterion[]; // Optional authored AC
  created_at: string; // ISO 8601
  completed_at: string | null;
}

/**
 * The `tracker` SSE section and the tracker slice of GET /api/state.
 * NOTE: GET /api/tracker returns a BARE TrackerItem[] array (no wrapper).
 */
export interface TrackerSnapshot {
  items: TrackerItem[];
}

export interface AuditBacklogItem {
  status: '✅' | '🔵' | '⬜' | '⏸';
  tag: string; // e.g., "[sec]"
  title: string;
}

export interface AuditBacklogTier {
  tier: 'P0' | 'P1' | 'P2' | 'Needs decision';
  items: AuditBacklogItem[];
  done: number;
  inflight: number;
  todo: number;
  total: number;
}

export interface AuditBacklog {
  tiers: AuditBacklogTier[];
}

export interface Message {
  role: 'user' | 'assistant';
  text: string;
  timestamp: string; // ISO 8601
}

export interface OrchestratorEntry {
  id?: string;
  role?: string;
  activity?: string;
  phase?: string;
  age_seconds: number;
  stale: boolean;
  updated_at?: string;
}

export interface OrchestratorStatus {
  orchestrators: OrchestratorEntry[];
}

/**
 * Repo status entry from .watchdog-repos.json (list passthrough, or
 * {repo, state} pairs when the file holds an object).
 */
export interface RepoStatus {
  repo?: string;
  state?: unknown;
  [key: string]: unknown;
}

/**
 * GET /data response AND the `data` SSE section.
 * Note: `agents` is present on GET /data only; the SSE `data` section
 * (collectors._snapshot_data) omits it — agents arrive on their own
 * `agents` SSE section.
 */
export interface DashboardData {
  watchdog: HeartbeatStatus;
  monitor: HeartbeatStatus;
  agents?: Agent[];
  repos: RepoStatus[];
  events: string[];
  alerts: Alert;
  messages: Message[];
}

/** POST /submit success response. */
export interface SubmitResponse {
  ok: boolean;
}

export interface FullState {
  data: DashboardData;
  backlog: AuditBacklog;
  agents: Agent[];
  tracker: TrackerSnapshot;
  status: OrchestratorStatus;
  cost?: CostSummary;
}

/**
 * Cost summary from GET /api/cost.
 * Mirrors ui/cost.py get_cost_summary() docstring on branch
 * feat/wave14-u3-cost-collector — verbatim shape, NOT provisional.
 */
export interface CostModelStats {
  runs: number;
  tokens_in: number;
  tokens_out: number;
  verdicts: {
    OK: number;
    FAILED: number;
    EMPTY: number;
    HUNG: number;
  };
}

export interface CostDailyTotal {
  tokens_in: number;
  tokens_out: number;
}

export interface CostOverallScorecard {
  total_runs: number;
  ok_count: number;
  failed_count: number;
  empty_count: number;
  hung_count: number;
  ok_rate: number; // 0.0-1.0
  failed_rate: number;
  empty_rate: number;
  hung_rate: number;
}

export interface CostEstimate {
  input_cost: number; // dollars
  output_cost: number; // dollars
  total_cost: number; // dollars
}

export interface CostWeeklyData {
  tokens_in: number;
  tokens_out: number;
  model_tokens: Record<string, number>; // total tokens per model for the week
  cost: number; // dollars if pricing available, 0 otherwise
}

export interface CostVerdictWeightedCost {
  cost_per_ok: number;
  cost_per_failed: number;
  cost_per_empty: number;
  cost_per_hung: number;
}

export interface CostWaveData {
  tokens_in: number;
  tokens_out: number;
  model_tokens: Record<string, number>; // total tokens per model for the wave
  cost: number; // dollars if pricing available, 0 otherwise
}

export interface CostAgentData {
  tokens_in: number;
  tokens_out: number;
  model_tokens: Record<string, number>; // total tokens per model for this agent type
  runs: number;
  verdicts: {
    OK: number;
    FAILED: number;
    EMPTY: number;
    HUNG: number;
  };
  cost: number; // dollars if pricing available, 0 otherwise
}

export interface CostSummary {
  models: Record<string, CostModelStats>; // keyed by model id
  daily_totals: Record<string, CostDailyTotal>; // keyed by "YYYY-MM-DD"
  overall_scorecard: CostOverallScorecard;
  skipped_lines: number;
  has_pricing: boolean;
  estimates_by_model: Record<string, CostEstimate>; // empty when has_pricing is false
  per_week_costs: Record<string, CostWeeklyData>; // keyed by "YYYY-Www" (ISO week)
  per_wave_costs: Record<string, CostWaveData>; // keyed by "wave-N" (from ledger wave column)
  per_agent_costs: Record<string, CostAgentData>; // keyed by agent_type
  verdict_weighted_cost: CostVerdictWeightedCost;
  model_mix_trend: Record<string, Record<string, number>>; // keyed by "YYYY-MM-DD", values are model -> percentage
}

/**
 * One row on the Wave PR Board (GET /api/wave/prs).
 * `has_pr=false` rows are feat/* branches with no open PR yet (number is null).
 * `ci` is a color-independent rollup state paired with an icon+text in the UI.
 */
export interface WavePR {
  number: number | null;
  title: string;
  branch: string;
  url: string;
  ci: 'passing' | 'failing' | 'pending' | 'none';
  mergeable: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN' | string;
  is_draft: boolean;
  review_decision: string; // "APPROVED" | "CHANGES_REQUESTED" | "REVIEW_REQUIRED" | ""
  created_at: string; // ISO 8601, "" for branch-only rows
  blocker: string | null;
  has_pr: boolean;
}

/**
 * GET /api/wave/prs response.
 * When `available` is false (gh missing / un-authenticated), `error` carries a
 * human reason and `prs` is empty — the board renders a callout, not a crash.
 */
export interface WavePRBoardData {
  available: boolean;
  error: string | null;
  generated_at: string; // ISO 8601 UTC
  prs: WavePR[];
}

/**
 * One CI job from a workflow run, with an optional log excerpt.
 * Part of the wave failure drill-down (GET /api/wave/failure?pr=N).
 */
export interface WaveFailureJob {
  id: number;
  name: string;
  status: 'completed' | 'in_progress' | 'queued' | string;
  conclusion: 'success' | 'failure' | 'cancelled' | 'timed_out' | null | string;
  url: string;
  log_excerpt: string | null; // ~100 lines tail, null if fetch failed
}

/**
 * One workflow run (the latest run for a PR branch).
 * Part of the wave failure drill-down (GET /api/wave/failure?pr=N).
 */
export interface WaveFailureRun {
  id: string;
  name: string;
  status: 'completed' | 'in_progress' | 'queued' | string;
  conclusion: 'success' | 'failure' | 'cancelled' | 'timed_out' | null | string;
  url: string;
}

/**
 * GET /api/wave/failure?pr=N response.
 * When `available` is false (gh missing / un-authenticated), `error` carries a
 * human reason and `jobs` is empty — the drill-down renders a degraded state,
 * not a crash.
 */
export interface WaveFailureData {
  available: boolean;
  error: string | null;
  pr_number: number;
  branch: string;
  latest_run: WaveFailureRun | null;
  jobs: WaveFailureJob[];
}

/**
 * One agent entry in the wave dispatch snapshot (GET /api/wave/dispatch).
 * Shows phase, activity age, and token burn estimate in real-time.
 */
export interface WaveDispatchAgent {
  id: string; // agent id
  phase: string; // 'dispatch' | 'thinking' | 'tool-use' | 'stall' | 'done' | 'unknown'
  last_activity_age_sec: number; // seconds since last transcript update
  token_estimate: number; // estimated tokens consumed
  warnings?: string[]; // e.g., ["inactive >5min", "stalled >10min"]
}

/**
 * GET /api/wave/dispatch response — live per-agent phase and activity.
 * When `available` is false (no active workflow), agents array is empty.
 */
export interface WaveDispatchData {
  available: boolean;
  wave_phase: string | null; // e.g., "wave-rc.7: dispatch" or null
  agents: WaveDispatchAgent[];
  at: string; // ISO 8601 UTC
  error?: string; // optional error message if available=false
}

/**
 * One phase span in a Gantt row (GET /api/wave/gantt).
 */
export interface WaveGanttPhase {
  phase: string; // 'dispatch' | 'thinking' | 'tool-use' | 'stall' | 'done'
  start: string; // ISO 8601 UTC
  end: string; // ISO 8601 UTC
  duration_sec: number;
  token_estimate?: number;
}

/**
 * One agent row in Gantt chart (GET /api/wave/gantt).
 */
export interface WaveGanttAgent {
  id: string;
  phases: WaveGanttPhase[];
  total_duration_sec: number;
  status: 'running' | 'done' | 'stalled' | 'inactive';
}

/**
 * GET /api/wave/gantt — Gantt timeline data per-agent phase spans.
 * Shows agents as rows with phase timing bars for execution visibility.
 */
export interface WaveGanttData {
  available: boolean;
  wave_phase?: string; // e.g., "wave-rc.7: verify"
  agents: WaveGanttAgent[];
  at: string; // ISO 8601 UTC
  error?: string; // optional error message if available=false
}

/**
 * One audit tail event from GET /api/wave/audit-tail.
 * Union of audit backlog item or ledger verdict.
 */
export interface WaveAuditTailEvent {
  type: 'audit_backlog' | 'verdict';
  status?: string; // audit: '✅' | '🔵' | '⬜' | '⏸'
  tier?: string; // audit: 'P0' | 'P1' | 'P2'
  tag?: string; // audit: '[sec]' | '[ui]' | ...
  title?: string; // audit title
  timestamp?: string; // ISO 8601 or null
  verdict?: string; // ledger: 'OK' | 'FAILED' | 'EMPTY' | 'HUNG'
  agent?: string; // ledger: agent ID (short)
}

/**
 * GET /api/wave/audit-tail — latest audit/verification outcomes.
 * Shows recent audit backlog items and ledger verdicts.
 */
export interface WaveAuditTailData {
  available: boolean;
  audit_items: WaveAuditTailEvent[];
  at: string; // ISO 8601 UTC
  error?: string; // optional error message
}

/**
 * One agent's reasoning tail entry from GET /api/wave/reasoning-tail.
 * Shows per-agent latest transcript activity (redacted).
 */
export interface WaveReasoningAgent {
  id: string;
  phase: string; // 'dispatch' | 'thinking' | 'tool-use' | 'stall' | 'done'
  reasoning: string; // redacted brief activity summary (tool:X → result → thinking...)
  activity_age_sec: number;
  token_estimate: number;
  warnings?: string[]; // e.g., ['inactive >5min', 'stalled >10min']
}

/**
 * GET /api/wave/reasoning-tail — per-agent live reasoning transparency.
 * Shows latest transcript activity summary for each agent (redacted).
 */
export interface WaveReasoningTailData {
  available: boolean;
  agents: WaveReasoningAgent[];
  at: string; // ISO 8601 UTC
  error?: string; // optional error message
}

/**
 * One agent specialty quality metric from GET /api/wave/quality-scorecards.
 * Shows per-specialty success rate and retry/repair frequency.
 */
export interface QualityScorecardSpecialty {
  total_runs: number;
  success_count: number;
  failed_count: number;
  empty_count: number;
  hung_count: number;
  success_rate: number; // 0.0-1.0
  repair_count: number;
  retry_frequency: number; // 0.0-1.0
}

/**
 * One ranked entry in quality scorecard rankings.
 */
export interface QualityScorecardRanking {
  agent_type: string;
  success_rate?: number; // present in top_by_success
  retry_frequency?: number; // present in top_by_retry
  total_runs: number;
}

/**
 * GET /api/wave/quality-scorecards response.
 * Per-agent-specialty quality: success rates and retry/repair frequencies.
 */
export interface QualityScorecardData {
  specialties: Record<string, QualityScorecardSpecialty>; // keyed by agent_type
  top_by_success: QualityScorecardRanking[];
  top_by_retry: QualityScorecardRanking[];
  skipped_lines: number;
}

/**
 * SSE event sections emitted by GET /events.
 * Initial sections: data, backlog, agents, tracker, status
 * Added in U3: cost
 */
export type SSESection =
  | 'data'
  | 'backlog'
  | 'agents'
  | 'tracker'
  | 'status'
  | 'cost';

export interface SSEConnectionStatus {
  status: 'live' | 'reconnecting' | 'error';
  lastError?: string;
}

/**
 * Spec sharpness score: Quality indicators for a dispatch prompt (C1).
 * GET /api/quality/spec-sharpness?agent=<id>
 */
export interface SpecSharpnessSignals {
  directive_count: number;
  has_acceptance_criteria: boolean;
  file_specificity: number; // 0-1
  structured_content_ratio: number; // 0-1
  emphasis_markers: number;
}

export interface SpecSharpnessScore {
  level: 'Low' | 'Med' | 'High' | 'Excellent';
  score: number; // 0-100
  signals: SpecSharpnessSignals;
}

/**
 * File scope visualization: Intended vs actual files touched (C2).
 * GET /api/context/files?agent=<id>
 */
export interface FileScopeDrift {
  only_intended: string[];
  only_actual: string[];
}

export interface FileScopeData {
  intended_files: string[];
  actual_files: string[];
  coverage: number; // 0-1
  drift: FileScopeDrift;
}

/**
 * Benchmark result from GET /api/bench.
 * Records model performance: accuracy, token usage, latency, cost estimates.
 */
export interface BenchResult {
  timestamp: number; // Unix epoch seconds
  model: string;
  tasks?: number;
  passed?: number;
  accuracy: number; // 0.0-1.0
  total_tokens?: number;
  avg_latency_ms?: number;
  cost_estimate?: number;
}

/**
 * GET /api/bench/compare — model comparison summary.
 * Maps model name to its latest stats.
 */
export type BenchComparison = Record<string, BenchResult>;

/**
 * One exception row from state/merge-queue/exceptions.jsonl
 * (ts: ISO timestamp, pr: PR number, kind: exception type)
 */
export interface QueueException {
  ts: string; // ISO 8601 timestamp
  pr: number;
  kind: string; // e.g., "merge_conflict", "ci_failure", "blocked"
}

/**
 * GET /api/queue — merge-queue operator panel data
 * queue_depth: # open PRs labeled merge-queue
 * batch_state: # open merge-queue-batch PRs
 * last_advance_age: seconds since last heartbeat mtime
 * last_advance_degraded: true if age > 10min
 * exceptions: last N exception rows (most recent first)
 */
export interface QueuePanelData {
  queue_depth: number;
  batch_state: number;
  last_advance_age: number; // seconds, -1 if heartbeat missing
  last_advance_degraded: boolean;
  exceptions: QueueException[];
}

