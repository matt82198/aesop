/**
 * Shared test fixtures + the data-testid naming contract.
 *
 * TESTIDS is the SINGLE SOURCE OF TRUTH for data-testid values across the app.
 * U4–U7 components MUST attach these testids; U8's Playwright proofs assert
 * ONLY via these hooks (never CSS internals). Add new ids here first, then
 * use them from components — never inline a bare testid string.
 *
 * Fixture objects are realistic samples of every API payload type in
 * src/lib/types.ts. U4–U7 import these for component tests.
 */

import type {
  Agent,
  AgentDetail,
  AgentInspectorDetail,
  Alert,
  AuditBacklog,
  CostSummary,
  DashboardData,
  FullState,
  HeartbeatStatus,
  Message,
  OrchestratorStatus,
  QualityScorecardData,
  RepoStatus,
  TrackerItem,
  TrackerSnapshot,
  WavePR,
  WavePRBoardData,
  WaveFailureData,
  WaveFailureJob,
  WaveFailureRun,
  WaveDispatchData,
  WaveDispatchAgent,
  WaveGanttData,
  WaveGanttAgent,
  WaveAuditTailData,
  WaveAuditTailEvent,
  WaveReasoningTailData,
  WaveReasoningAgent,
} from '../lib/types';

/* ------------------------------------------------------------------ */
/* data-testid contract                                                */
/* ------------------------------------------------------------------ */

export const TESTIDS = {
  // Health header (sticky, always visible)
  healthHeader: 'health-header',
  healthWatchdog: 'health-watchdog',
  healthMonitor: 'health-monitor',
  healthOrchestrator: 'health-orchestrator',
  healthAgentsCount: 'health-agents-count',
  healthAlertsCount: 'health-alerts-count',
  healthAgentsRunning: 'health-agents-running',
  healthAgentsIdle: 'health-agents-idle',
  healthAgentsIssues: 'health-agents-issues',
  healthCost: 'health-cost',
  healthZone: 'health-zone',
  sseStatus: 'sse-status',
  themeToggle: 'theme-toggle',
  refreshButton: 'refresh-button',

  // Overview view
  viewOverview: 'view-overview',
  overviewMain: 'overview-main',
  overviewSidebar: 'overview-sidebar',
  waveTelemetryProgress: 'wave-telemetry-progress',
  agentsPanel: 'agents-panel',
  agentRow: 'agent-row',
  agentRowDetail: 'agent-row-detail',
  agentInspectOpen: 'agent-inspect-open',
  agentsSummaryCard: 'agents-summary-card',
  agentsGroup: 'agents-group',

  // Agent Inspector drawer (read-only agent detail + transcript tail)
  agentInspector: 'agent-inspector',
  agentInspectorClose: 'agent-inspector-close',
  agentInspectorStatus: 'agent-inspector-status',
  agentInspectorTranscript: 'agent-inspector-transcript',
  agentInspectorTail: 'agent-inspector-tail-entry',
  agentInspectorLoading: 'agent-inspector-loading',
  agentInspectorError: 'agent-inspector-error',
  agentInspectorEmpty: 'agent-inspector-empty',
  alertLine: 'alert-line',
  alertsPanel: 'alerts-panel',
  eventsFeed: 'events-feed',
  reposPanel: 'repos-panel',
  inboxForm: 'inbox-form',
  inboxInput: 'inbox-input',
  inboxSubmit: 'inbox-submit',

  // Work view
  viewWork: 'view-work',
  waveTelemetryCost: 'wave-telemetry-cost',
  qualityScorecards: 'quality-scorecards',
  trackerBoard: 'tracker-board',
  trackerLane: 'tracker-lane',
  trackerCard: 'tracker-card',
  trackerForm: 'tracker-form',
  trackerFormTitle: 'tracker-form-title',
  trackerFormSubmit: 'tracker-form-submit',
  trackerFormAddAC: 'tracker-form-add-ac',
  trackerFormRemoveAC: 'tracker-form-remove-ac',
  trackerEditAC: 'tracker-edit-ac',
  backlogPanel: 'backlog-panel',

  // Activity view
  viewActivity: 'view-activity',
  timeline: 'timeline',
  timelineBar: 'timeline-bar',
  messagesTail: 'messages-tail',
  messagesFollowToggle: 'messages-follow-toggle',
  dispatchPanel: 'dispatch-panel',
  dispatchPanelUnavailable: 'dispatch-panel-unavailable',
  dispatchAgentRow: 'dispatch-agent-row',
  dispatchAgentPhase: 'dispatch-agent-phase',
  dispatchAgentAge: 'dispatch-agent-age',
  dispatchAgentTokens: 'dispatch-agent-tokens',
  ganttTimeline: 'gantt-timeline',
  ganttRow: 'gantt-row',
  ganttPhaseBar: 'gantt-phase-bar',
  auditTail: 'audit-tail',
  auditTailItem: 'audit-tail-item',
  reasoningTail: 'reasoning-tail',
  reasoningTailAgent: 'reasoning-tail-agent',

  // Cost view
  viewCost: 'view-cost',
  costTable: 'cost-table',
  costChart: 'cost-chart',
  scorecard: 'scorecard',
  weeklyCostSummary: 'weekly-cost-summary',
  verdictCostMetrics: 'verdict-cost-metrics',
  modelMixChart: 'model-mix-chart',
  costAnalyticsPanel: 'cost-analytics-panel',
  waveAgentBreakdown: 'wave-agent-breakdown',

  // PR Board view
  viewPRBoard: 'view-prboard',
  prBoardTable: 'prboard-table',
  prBoardRow: 'prboard-row',
  prBoardCi: 'prboard-ci',
  prBoardEmpty: 'prboard-empty',
  prBoardError: 'prboard-error',
  prBoardLoading: 'prboard-loading',
  prBoardRefresh: 'prboard-refresh',
  prBoardTogglePRless: 'prboard-toggle-prless',

  // Failure Drill-down component
  failureDrilldown: 'failure-drilldown',
  failureDrilldownToggle: 'failure-drilldown-toggle',
  failureDrilldownContent: 'failure-drilldown-content',
  failureDrilldownLoading: 'failure-drilldown-loading',
  failureDrilldownError: 'failure-drilldown-error',
  failureDrilldownUnavailable: 'failure-drilldown-unavailable',
  failureDrilldownEmpty: 'failure-drilldown-empty',
  failureDrilldownRun: 'failure-drilldown-run',
  failureDrilldownJob: 'failure-drilldown-job',
  failureDrilldownLogExcerpt: 'failure-drilldown-log-excerpt',
} as const;

export type TestId = (typeof TESTIDS)[keyof typeof TESTIDS];

/* ------------------------------------------------------------------ */
/* Fixtures                                                            */
/* ------------------------------------------------------------------ */

export const fixtureWatchdog: HeartbeatStatus = {
  alive: 'ALIVE',
  age: 3,
  threshold: 300,
};

export const fixtureWatchdogStale: HeartbeatStatus = {
  alive: 'STALE',
  age: 642,
  threshold: 300,
};

export const fixtureMonitor: HeartbeatStatus = {
  alive: 'ALIVE',
  age: 45,
  threshold: 3600,
};

export const fixtureMonitorNotRunning: HeartbeatStatus = {
  alive: 'not running',
  age: -1,
  threshold: 3600,
};

export const fixtureAgents: Agent[] = [
  {
    id: 'a77b995bcdb95',
    project: 'aesop',
    status: 'running',
    age_s: 12,
    hint: 'wave-14 U4 overview components',
    startedAt: '2026-07-13T14:02:11.000Z',
    lastActivity: '2026-07-13T14:31:47.000Z',
    runtimeSeconds: 1776,
    tokensUsed: 48213,
    taskLabel: 'Wave-14 unit U4 (overview view components) for aesop.',
    promptFull: 'Wave-14 unit U4 (overview view components) for aesop. Read the plan first.',
  },
  {
    id: 'b12c4d99ef012',
    project: 'aesop',
    status: 'idle',
    age_s: 341,
    hint: 'tracker lane bucketing tests',
    startedAt: '2026-07-13T13:40:00.000Z',
    lastActivity: '2026-07-13T14:26:02.000Z',
    runtimeSeconds: 2762,
    tokensUsed: 102455,
    taskLabel: 'Wave-14 unit U5 (work view components) for aesop.',
    promptFull: 'Wave-14 unit U5 (work view components) for aesop.',
  },
  {
    id: 'c99ff00aa1122',
    project: 'tr-sample-tracker',
    status: 'SUSPICIOUS',
    age_s: 45,
    hint: 'unexpected file write outside worktree',
    startedAt: '2026-07-13T14:20:00.000Z',
    lastActivity: '2026-07-13T14:31:15.000Z',
    runtimeSeconds: 675,
    tokensUsed: 8102,
    taskLabel: 'Fix flaky test in sample tracker suite.',
    promptFull: 'Fix flaky test in sample tracker suite.',
  },
];

// All agents healthy (running/idle only) — used to assert the Warnings badge
// renders in its zero/success state (item 1a, dashboard-polish-nits).
export const fixtureAgentsNoIssues: Agent[] = [
  fixtureAgents[0],
  fixtureAgents[1],
];

export const fixtureAgentDetail: AgentDetail = {
  id: 'a77b995bcdb95',
  dispatch_prompt:
    'Wave-14 unit U4 (overview view components) for aesop. Read the plan FIRST, then implement AgentsPanel, AlertsPanel, EventsFeed with tests.',
  dispatcher: 'main thread',
  model: 'claude-haiku-4-5-20251001',
  message_count: 87,
  first_seen: 1783346531,
  last_activity: 1783348307,
};

export const fixtureAgentInspector: AgentInspectorDetail = {
  id: 'a77b995bcdb95',
  dispatch_prompt:
    'Wave-14 unit U4 (overview view components) for aesop. Read the plan FIRST, then implement AgentsPanel, AlertsPanel, EventsFeed with tests.',
  dispatcher: 'main thread',
  model: 'claude-haiku-4-5-20251001',
  message_count: 87,
  first_seen: 1783346531,
  last_activity: 1783348307,
  tail_truncated: true,
  transcript_tail: [
    { type: 'user', text: 'Start on unit U4 — read the plan first, then build the panels.' },
    { type: 'assistant', text: 'Reading the plan, then scaffolding AgentsPanel.' },
    { type: 'assistant', text: '[tool_use: Write]' },
    { type: 'tool_result', text: '[tool_result]' },
    { type: 'assistant', text: 'AgentsPanel + tests done; vitest green.' },
  ],
};

export const fixtureAlerts: Alert = {
  count: 2,
  lines: [
    '2026-07-13T09:11:02Z HIGH agent-c99ff00 wrote outside its worktree: <REPO>/ui/serve.py',
    '2026-07-13T11:45:38Z MED agent-b12c4d9 3 consecutive test failures on feat/wave14-u5-work',
  ],
};

export const fixtureAlertsEmpty: Alert = { count: 0, lines: [] };

export const fixtureRepos: RepoStatus[] = [
  { repo: 'aesop', state: 'clean' },
  { repo: 'tr-sample-tracker', state: 'dirty: 3 files' },
  { repo: 'ecm-ai', state: 'clean' },
];

export const fixtureEvents: string[] = [
  '2026-07-13 14:00:01 BACKUP aesop -> bundle OK (12.4 MB)',
  '2026-07-13 14:00:04 BACKUP tr-sample-tracker -> bundle OK (3.1 MB)',
  '2026-07-13 14:05:00 SCAN secret_scan --staged exit 0',
  '2026-07-13 14:10:22 PUSH aesop feat/wave14-u1-foundation OK',
];

export const fixtureMessages: Message[] = [
  {
    role: 'user',
    text: 'Run wave 14: dashboard rewrite, start with the foundation unit.',
    timestamp: '2026-07-13T14:00:12.000Z',
  },
  {
    role: 'assistant',
    text: 'Dispatching U1 (foundation) to a worktree agent; U3 cost collector runs in parallel.',
    timestamp: '2026-07-13T14:01:03.000Z',
  },
  {
    role: 'assistant',
    text: 'U1 scaffold complete: vite build green, vitest 41 passing. Moving to fixtures.',
    timestamp: '2026-07-13T14:29:44.000Z',
  },
];

export const fixtureDashboardData: DashboardData = {
  watchdog: fixtureWatchdog,
  monitor: fixtureMonitor,
  agents: fixtureAgents,
  repos: fixtureRepos,
  events: fixtureEvents,
  alerts: fixtureAlerts,
  messages: fixtureMessages,
};

export const fixtureBacklog: AuditBacklog = {
  tiers: [
    {
      tier: 'P0',
      items: [
        { status: '✅', tag: '[sec]', title: 'Origin fail-closed on /api/session' },
        { status: '🔵', tag: '[ui]', title: 'Dashboard rewrite foundation (U1)' },
        { status: '⬜', tag: '[ui]', title: 'Cutover / to dist index (U9)' },
      ],
      done: 1,
      inflight: 1,
      todo: 1,
      total: 3,
    },
    {
      tier: 'P1',
      items: [
        { status: '⬜', tag: '[perf]', title: 'SSE keepalive tuning' },
        { status: '⏸', tag: '[arch]', title: 'Hierarchical orchestration seams' },
      ],
      done: 0,
      inflight: 0,
      todo: 1,
      total: 2,
    },
  ],
};

export const fixtureTrackerItems: TrackerItem[] = [
  {
    id: '3f9a1b2c4d5e',
    title: 'Dashboard rewrite: foundation scaffold',
    priority: 'P0',
    status: 'in-progress',
    lane: 'in-progress',
    source: 'wave14-plan',
    tags: ['ui', 'wave-14'],
    notes: 'U1 trunk unit; U4-U7 build on its types/fixtures/shell.',
    pr_link: 'https://github.com/matt82198/aesop/pull/113',
    acceptanceCriteria: [
      { statement: 'Vite build passes', verifiable_by: 'npm run build' },
      { statement: 'Tests green', verifiable_by: 'vitest' },
    ],
    created_at: '2026-07-12T18:30:00Z',
    completed_at: null,
  },
  {
    id: '8c7d6e5f4a3b',
    title: 'Cost collector parses OUTCOMES-LEDGER.md',
    priority: 'P1',
    status: 'done',
    lane: 'done',
    source: 'wave14-plan',
    tags: ['cost'],
    notes: null,
    pr_link: null,
    created_at: '2026-07-12T18:31:00Z',
    completed_at: '2026-07-13T09:15:00Z',
  },
  {
    id: '1a2b3c4d5e6f',
    title: 'Agent timeline read-only v1',
    priority: 'P2',
    status: 'todo',
    lane: 'ranked',
    source: 'audit-backlog-migration',
    tags: ['activity'],
    notes: null,
    pr_link: null,
    created_at: '2026-07-11T10:00:00Z',
    completed_at: null,
  },
  {
    id: '9e8d7c6b5a40',
    title: 'Replay slider for agent timeline',
    priority: 'P2',
    status: 'todo',
    lane: 'proposed',
    source: 'ideation',
    tags: ['activity', 'later'],
    notes: 'v2 — read-only timeline ships first.',
    pr_link: null,
    created_at: '2026-07-11T10:05:00Z',
    completed_at: null,
  },
  {
    id: 'aa11bb22cc33',
    title: 'Old dashboard patch attempt (superseded)',
    priority: 'P2',
    status: 'archived',
    lane: 'done',
    source: 'manual',
    tags: [],
    notes: null,
    pr_link: 'javascript:alert(1)', // deliberately hostile: must render inert via sanitizeUrl
    created_at: '2026-07-01T08:00:00Z',
    completed_at: '2026-07-02T08:00:00Z',
  },
];

export const fixtureTracker: TrackerSnapshot = { items: fixtureTrackerItems };

export const fixtureStatus: OrchestratorStatus = {
  orchestrators: [
    {
      id: 'main',
      role: 'orchestrator',
      age_seconds: 42,
      stale: false,
      updated_at: '2026-07-13T14:31:00Z',
    },
  ],
};

export const fixtureCost: CostSummary = {
  models: {
    'claude-haiku-4-5-20251001': {
      runs: 128,
      tokens_in: 2140050,
      tokens_out: 512300,
      verdicts: { OK: 119, FAILED: 6, EMPTY: 2, HUNG: 1 },
    },
    'claude-sonnet-4-5-20250929': {
      runs: 14,
      tokens_in: 890120,
      tokens_out: 210440,
      verdicts: { OK: 13, FAILED: 1, EMPTY: 0, HUNG: 0 },
    },
  },
  daily_totals: {
    '2026-07-11': { tokens_in: 1204000, tokens_out: 280100 },
    '2026-07-12': { tokens_in: 986170, tokens_out: 262300 },
    '2026-07-13': { tokens_in: 840000, tokens_out: 180340 },
  },
  overall_scorecard: {
    total_runs: 142,
    ok_count: 132,
    failed_count: 7,
    empty_count: 2,
    hung_count: 1,
    ok_rate: 0.9296,
    failed_rate: 0.0493,
    empty_rate: 0.0141,
    hung_rate: 0.007,
  },
  skipped_lines: 3,
  has_pricing: false,
  estimates_by_model: {},
  per_week_costs: {
    '2026-W28': {
      tokens_in: 1204000,
      tokens_out: 280100,
      model_tokens: {
        'claude-haiku-4-5-20251001': 1484100,
        'claude-sonnet-4-5-20250929': 100560,
      },
      cost: 0,
    },
    '2026-W29': {
      tokens_in: 1826170,
      tokens_out: 442640,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2268810,
        'claude-sonnet-4-5-20250929': 999560,
      },
      cost: 0,
    },
  },
  verdict_weighted_cost: {
    cost_per_ok: 24.5,
    cost_per_failed: 182.1,
    cost_per_empty: 1365.8,
    cost_per_hung: 2731.6,
  },
  per_wave_costs: {
    'wave-14': {
      tokens_in: 2030170,
      tokens_out: 442640,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2268810,
        'claude-sonnet-4-5-20250929': 203000,
      },
      cost: 0,
    },
    'wave-13': {
      tokens_in: 1000000,
      tokens_out: 280100,
      model_tokens: {
        'claude-haiku-4-5-20251001': 1484100,
        'claude-sonnet-4-5-20250929': 796000,
      },
      cost: 0,
    },
  },
  per_agent_costs: {
    'Agent': {
      tokens_in: 2140050,
      tokens_out: 512300,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2652350,
        'claude-sonnet-4-5-20250929': 0,
      },
      runs: 128,
      verdicts: { OK: 119, FAILED: 6, EMPTY: 2, HUNG: 1 },
      cost: 0,
    },
    'main thread': {
      tokens_in: 890120,
      tokens_out: 210440,
      model_tokens: {
        'claude-haiku-4-5-20251001': 0,
        'claude-sonnet-4-5-20250929': 1100560,
      },
      runs: 14,
      verdicts: { OK: 13, FAILED: 1, EMPTY: 0, HUNG: 0 },
      cost: 0,
    },
  },
  model_mix_trend: {
    '2026-07-11': {
      'claude-haiku-4-5-20251001': 81.2,
      'claude-sonnet-4-5-20250929': 18.8,
    },
    '2026-07-12': {
      'claude-haiku-4-5-20251001': 78.9,
      'claude-sonnet-4-5-20250929': 21.1,
    },
    '2026-07-13': {
      'claude-haiku-4-5-20251001': 82.4,
      'claude-sonnet-4-5-20250929': 17.6,
    },
  },
};

export const fixtureCostWithPricing: CostSummary = {
  ...fixtureCost,
  has_pricing: true,
  estimates_by_model: {
    'claude-haiku-4-5-20251001': {
      input_cost: 2.14,
      output_cost: 2.05,
      total_cost: 4.19,
    },
    'claude-sonnet-4-5-20250929': {
      input_cost: 2.67,
      output_cost: 3.16,
      total_cost: 5.83,
    },
  },
  per_week_costs: {
    '2026-W28': {
      tokens_in: 1204000,
      tokens_out: 280100,
      model_tokens: {
        'claude-haiku-4-5-20251001': 1484100,
        'claude-sonnet-4-5-20250929': 100560,
      },
      cost: 9.87,
    },
    '2026-W29': {
      tokens_in: 1826170,
      tokens_out: 442640,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2268810,
        'claude-sonnet-4-5-20250929': 999560,
      },
      cost: 15.21,
    },
  },
  per_wave_costs: {
    'wave-14': {
      tokens_in: 2030170,
      tokens_out: 442640,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2268810,
        'claude-sonnet-4-5-20250929': 203000,
      },
      cost: 11.02,
    },
    'wave-13': {
      tokens_in: 1000000,
      tokens_out: 280100,
      model_tokens: {
        'claude-haiku-4-5-20251001': 1484100,
        'claude-sonnet-4-5-20250929': 796000,
      },
      cost: 8.78,
    },
  },
  per_agent_costs: {
    'Agent': {
      tokens_in: 2140050,
      tokens_out: 512300,
      model_tokens: {
        'claude-haiku-4-5-20251001': 2652350,
        'claude-sonnet-4-5-20250929': 0,
      },
      runs: 128,
      verdicts: { OK: 119, FAILED: 6, EMPTY: 2, HUNG: 1 },
      cost: 8.91,
    },
    'main thread': {
      tokens_in: 890120,
      tokens_out: 210440,
      model_tokens: {
        'claude-haiku-4-5-20251001': 0,
        'claude-sonnet-4-5-20250929': 1100560,
      },
      runs: 14,
      verdicts: { OK: 13, FAILED: 1, EMPTY: 0, HUNG: 0 },
      cost: 3.91,
    },
  },
  verdict_weighted_cost: {
    cost_per_ok: 0.077,
    cost_per_failed: 1.418,
    cost_per_empty: 4.72,
    cost_per_hung: 10.02,
  },
};

export const fixtureQualityScorecard: QualityScorecardData = {
  specialties: {
    haiku: {
      total_runs: 150,
      success_count: 142,
      failed_count: 6,
      empty_count: 2,
      hung_count: 0,
      success_rate: 0.9467,
      repair_count: 8,
      retry_frequency: 0.0533,
    },
    sonnet: {
      total_runs: 78,
      success_count: 76,
      failed_count: 2,
      empty_count: 0,
      hung_count: 0,
      success_rate: 0.9744,
      repair_count: 3,
      retry_frequency: 0.0385,
    },
    orchestrator: {
      total_runs: 34,
      success_count: 33,
      failed_count: 1,
      empty_count: 0,
      hung_count: 0,
      success_rate: 0.9706,
      repair_count: 1,
      retry_frequency: 0.0294,
    },
  },
  top_by_success: [
    { agent_type: 'sonnet', success_rate: 0.9744, total_runs: 78 },
    { agent_type: 'orchestrator', success_rate: 0.9706, total_runs: 34 },
    { agent_type: 'haiku', success_rate: 0.9467, total_runs: 150 },
  ],
  top_by_retry: [
    { agent_type: 'haiku', retry_frequency: 0.0533, total_runs: 150 },
    { agent_type: 'sonnet', retry_frequency: 0.0385, total_runs: 78 },
    { agent_type: 'orchestrator', retry_frequency: 0.0294, total_runs: 34 },
  ],
  skipped_lines: 0,
};

export const fixtureQualityScorecardEmpty: QualityScorecardData = {
  specialties: {},
  top_by_success: [],
  top_by_retry: [],
  skipped_lines: 0,
};

export const fixtureWavePRs: WavePR[] = [
  {
    number: 173,
    title: 'feat: Live Wave PR Board view',
    branch: 'feat/wave30-pr-board',
    url: 'https://github.com/matt82198/aesop/pull/173',
    ci: 'passing',
    mergeable: 'MERGEABLE',
    is_draft: false,
    review_decision: 'REVIEW_REQUIRED',
    created_at: '2026-07-17T09:00:00Z',
    blocker: 'Review required',
    has_pr: true,
  },
  {
    number: 172,
    title: 'fix: collector fail-open on ledger parse error',
    branch: 'feat/wave30-ledger-failopen',
    url: 'https://github.com/matt82198/aesop/pull/172',
    ci: 'failing',
    mergeable: 'CONFLICTING',
    is_draft: false,
    review_decision: '',
    created_at: '2026-07-16T18:30:00Z',
    blocker: 'CI failing',
    has_pr: true,
  },
  {
    number: 171,
    title: 'wip: hierarchical orchestration seams',
    branch: 'feat/wave30-orch-seams',
    url: 'https://github.com/matt82198/aesop/pull/171',
    ci: 'pending',
    mergeable: 'MERGEABLE',
    is_draft: true,
    review_decision: '',
    created_at: '2026-07-17T07:15:00Z',
    blocker: 'Draft — not ready for review',
    has_pr: true,
  },
  {
    number: null,
    title: 'feat/wave30-cost-pricing',
    branch: 'feat/wave30-cost-pricing',
    url: '',
    ci: 'none',
    mergeable: 'UNKNOWN',
    is_draft: false,
    review_decision: '',
    created_at: '',
    blocker: 'No PR opened yet',
    has_pr: false,
  },
];

export const fixtureWavePRBoard: WavePRBoardData = {
  available: true,
  error: null,
  generated_at: '2026-07-17T09:05:00Z',
  prs: fixtureWavePRs,
};

export const fixtureWavePRBoardEmpty: WavePRBoardData = {
  available: true,
  error: null,
  generated_at: '2026-07-17T09:05:00Z',
  prs: [],
};

export const fixtureWavePRBoardUnavailable: WavePRBoardData = {
  available: false,
  error: 'GitHub CLI is not authenticated (run: gh auth login).',
  generated_at: '2026-07-17T09:05:00Z',
  prs: [],
};

export const fixtureFullState: FullState = {
  data: fixtureDashboardData,
  backlog: fixtureBacklog,
  agents: fixtureAgents,
  tracker: fixtureTracker,
  status: fixtureStatus,
  cost: fixtureCost,
};

export const fixtureWaveFailureRun: WaveFailureRun = {
  id: 'run-12345',
  name: 'CI / test',
  status: 'completed',
  conclusion: 'failure',
  url: 'https://github.com/matt82198/aesop/actions/runs/12345',
};

export const fixtureWaveFailureJobs: WaveFailureJob[] = [
  {
    id: 1001,
    name: 'test (ubuntu)',
    status: 'completed',
    conclusion: 'failure',
    url: 'https://github.com/matt82198/aesop/actions/runs/12345/job/1001',
    log_excerpt:
      'error: test suite failed\n' +
      'FAILED tests/test_serve.py::test_api_state_response_shape\n' +
      'AssertionError: expected "wave" in response\n' +
      'Expected dict to contain "wave" key\n' +
      'Actual keys: ["available", "error"]\n',
  },
  {
    id: 1002,
    name: 'lint (ubuntu)',
    status: 'completed',
    conclusion: 'success',
    url: 'https://github.com/matt82198/aesop/actions/runs/12345/job/1002',
    log_excerpt: null,
  },
];

export const fixtureWaveFailureData: WaveFailureData = {
  available: true,
  error: null,
  pr_number: 172,
  branch: 'feat/wave30-ledger-failopen',
  latest_run: fixtureWaveFailureRun,
  jobs: fixtureWaveFailureJobs,
};

export const fixtureWaveFailureDataUnavailable: WaveFailureData = {
  available: false,
  error: 'GitHub CLI is not authenticated (run: gh auth login).',
  pr_number: 172,
  branch: '',
  latest_run: null,
  jobs: [],
};

export const fixtureWaveFailureDataEmpty: WaveFailureData = {
  available: true,
  error: null,
  pr_number: 173,
  branch: 'feat/wave30-pr-board',
  latest_run: null,
  jobs: [],
};

export const fixtureWaveDispatchAgent: WaveDispatchAgent = {
  id: 'fleet-fix-0',
  phase: 'tool-use',
  last_activity_age_sec: 3,
  token_estimate: 145000,
};

export const fixtureWaveDispatchAgents: WaveDispatchAgent[] = [
  {
    id: 'fleet-fix-0',
    phase: 'tool-use',
    last_activity_age_sec: 3,
    token_estimate: 145000,
  },
  {
    id: 'fleet-fix-1',
    phase: 'stall',
    last_activity_age_sec: 420,
    token_estimate: 89000,
    warnings: ['inactive >5min'],
  },
  {
    id: 'fleet-review-0',
    phase: 'thinking',
    last_activity_age_sec: 12,
    token_estimate: 76500,
  },
];

export const fixtureWaveDispatch: WaveDispatchData = {
  available: true,
  wave_phase: 'wave-rc.7: dispatch',
  agents: fixtureWaveDispatchAgents,
  at: '2026-07-17T20:24:50Z',
};

export const fixtureWaveDispatchUnavailable: WaveDispatchData = {
  available: false,
  wave_phase: null,
  agents: [],
  at: '2026-07-17T20:24:50Z',
};

export const fixtureWaveGanttAgent: WaveGanttAgent = {
  id: 'fleet-fix-0',
  phases: [
    {
      phase: 'dispatch',
      start: '2026-07-17T20:24:00Z',
      end: '2026-07-17T20:24:15Z',
      duration_sec: 15,
      token_estimate: 5000,
    },
    {
      phase: 'thinking',
      start: '2026-07-17T20:24:15Z',
      end: '2026-07-17T20:24:45Z',
      duration_sec: 30,
      token_estimate: 25000,
    },
    {
      phase: 'tool-use',
      start: '2026-07-17T20:24:45Z',
      end: '2026-07-17T20:24:50Z',
      duration_sec: 5,
      token_estimate: 3000,
    },
  ],
  total_duration_sec: 50,
  status: 'running',
};

export const fixtureWaveGantt: WaveGanttData = {
  available: true,
  wave_phase: 'wave-rc.7: verify',
  agents: [
    fixtureWaveGanttAgent,
    {
      id: 'fleet-fix-1',
      phases: [
        {
          phase: 'dispatch',
          start: '2026-07-17T20:24:05Z',
          end: '2026-07-17T20:24:20Z',
          duration_sec: 15,
          token_estimate: 4000,
        },
        {
          phase: 'stall',
          start: '2026-07-17T20:24:20Z',
          end: '2026-07-17T20:24:50Z',
          duration_sec: 30,
          token_estimate: 0,
        },
      ],
      total_duration_sec: 45,
      status: 'stalled',
    },
    {
      id: 'fleet-review-0',
      phases: [
        {
          phase: 'thinking',
          start: '2026-07-17T20:24:10Z',
          end: '2026-07-17T20:24:38Z',
          duration_sec: 28,
          token_estimate: 20000,
        },
      ],
      total_duration_sec: 28,
      status: 'done',
    },
  ],
  at: '2026-07-17T20:24:50Z',
};

export const fixtureWaveGanttUnavailable: WaveGanttData = {
  available: false,
  wave_phase: undefined,
  agents: [],
  at: '2026-07-17T20:24:50Z',
};

export const fixtureWaveAuditTailEvents: WaveAuditTailEvent[] = [
  {
    type: 'audit_backlog',
    status: '✅',
    tier: 'P0',
    tag: '[sec]',
    title: 'CSRF validation chain fixed',
    timestamp: '2026-07-21T10:30:00Z',
  },
  {
    type: 'verdict',
    agent: 'fleet-fix-0',
    verdict: 'OK',
    timestamp: '2026-07-21T10:28:15Z',
  },
  {
    type: 'audit_backlog',
    status: '🔵',
    tier: 'P1',
    tag: '[perf]',
    title: 'SSE keepalive tuning in progress',
    timestamp: '2026-07-21T09:45:00Z',
  },
  {
    type: 'verdict',
    agent: 'fleet-fix-1',
    verdict: 'FAILED',
    timestamp: '2026-07-21T09:40:22Z',
  },
  {
    type: 'audit_backlog',
    status: '⬜',
    tier: 'P2',
    tag: '[ui]',
    title: 'Timeline component edge case handling',
    timestamp: '2026-07-21T08:15:00Z',
  },
];

export const fixtureWaveAuditTail: WaveAuditTailData = {
  available: true,
  audit_items: fixtureWaveAuditTailEvents,
  at: '2026-07-21T10:35:00Z',
};

export const fixtureWaveAuditTailUnavailable: WaveAuditTailData = {
  available: false,
  audit_items: [],
  at: '2026-07-21T10:35:00Z',
};

export const fixtureWaveReasoningAgents: WaveReasoningAgent[] = [
  {
    id: 'fleet-fix-0',
    phase: 'tool-use',
    reasoning: 'thinking → tool:edit → result → thinking',
    activity_age_sec: 3,
    token_estimate: 145000,
  },
  {
    id: 'fleet-fix-1',
    phase: 'stall',
    reasoning: 'tool:bash → result (error)',
    activity_age_sec: 420,
    token_estimate: 89000,
    warnings: ['inactive >5min', 'stalled >10min'],
  },
  {
    id: 'fleet-review-0',
    phase: 'thinking',
    reasoning: 'prompt → thinking',
    activity_age_sec: 12,
    token_estimate: 76500,
  },
];

export const fixtureWaveReasoningTail: WaveReasoningTailData = {
  available: true,
  agents: fixtureWaveReasoningAgents,
  at: '2026-07-21T10:35:00Z',
};

export const fixtureWaveReasoningTailUnavailable: WaveReasoningTailData = {
  available: false,
  agents: [],
  at: '2026-07-21T10:35:00Z',
};
