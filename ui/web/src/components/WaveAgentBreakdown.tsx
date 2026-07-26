/**
 * Wave & Agent Cost Breakdown — detailed per-wave/per-agent/per-model cost analysis.
 *
 * Displays:
 * - Cost per wave (from per_wave_costs)
 * - Cost per agent type (from per_agent_costs)
 * - Per-model distribution within each category
 *
 * Renders as an info-dense table with expandable rows showing model breakdown.
 */

import type { CostSummary } from '../lib/types';
import { useState } from 'react';
import { TESTIDS } from '../test/fixtures';
import './WaveAgentBreakdown.css';

interface WaveAgentBreakdownProps {
  cost: CostSummary;
}

interface ExpandedRows {
  [key: string]: boolean;
}

export function WaveAgentBreakdown({ cost }: WaveAgentBreakdownProps) {
  const [expandedWaves, setExpandedWaves] = useState<ExpandedRows>({});
  const [expandedAgents, setExpandedAgents] = useState<ExpandedRows>({});

  const toggleWave = (wave: string) => {
    setExpandedWaves((prev) => ({
      ...prev,
      [wave]: !prev[wave],
    }));
  };

  const toggleAgent = (agent: string) => {
    setExpandedAgents((prev) => ({
      ...prev,
      [agent]: !prev[agent],
    }));
  };

  // Format tokens for display
  const formatTokens = (tokens: number): string => {
    if (tokens >= 1_000_000) {
      return (tokens / 1_000_000).toFixed(2) + 'M';
    }
    if (tokens >= 1_000) {
      return (tokens / 1_000).toFixed(1) + 'K';
    }
    return tokens.toString();
  };

  // Format cost for display
  const formatCost = (cost: number): string => {
    return cost > 0 ? `$${cost.toFixed(2)}` : '—';
  };

  const waves = Object.entries(cost.per_wave_costs || {}).sort(([a], [b]) => {
    const aNum = parseInt(a.replace('wave-', ''), 10);
    const bNum = parseInt(b.replace('wave-', ''), 10);
    return bNum - aNum; // descending (latest first)
  });

  const agents = Object.entries(cost.per_agent_costs || {});

  return (
    <section
      className="wave-agent-breakdown"
      data-testid={TESTIDS.waveAgentBreakdown}
      aria-label="Cost breakdown by wave and agent"
    >
      <div className="breakdown-section">
        <h4>Cost per Wave</h4>
        {waves.length === 0 ? (
          <div className="breakdown-empty">
            <p>No wave-level cost data available in ledger</p>
          </div>
        ) : (
          <table className="breakdown-table">
            <thead>
              <tr>
                <th className="col-wave">Wave</th>
                <th className="col-tokens">Tokens In</th>
                <th className="col-tokens">Tokens Out</th>
                <th className="col-cost">Cost</th>
                <th className="col-action" />
              </tr>
            </thead>
            <tbody>
              {waves.map(([wave, data]) => (
                <tbody key={wave}>
                  <tr className="breakdown-row" onClick={() => toggleWave(wave)}>
                    <td className="col-wave">{wave}</td>
                    <td className="col-tokens">{formatTokens(data.tokens_in)}</td>
                    <td className="col-tokens">{formatTokens(data.tokens_out)}</td>
                    <td className="col-cost">{formatCost(data.cost)}</td>
                    <td className="col-action">
                      <button
                        className="expand-toggle"
                        aria-label={expandedWaves[wave] ? 'Collapse' : 'Expand'}
                        aria-expanded={expandedWaves[wave] === true}
                      >
                        {expandedWaves[wave] ? '▼' : '▶'}
                      </button>
                    </td>
                  </tr>
                  {expandedWaves[wave] && (
                    <tr className="breakdown-detail">
                      <td colSpan={5}>
                        <div className="detail-content">
                          <h5>Model Breakdown</h5>
                          <table className="detail-table">
                            <thead>
                              <tr>
                                <th>Model</th>
                                <th>Tokens</th>
                                <th className="col-percent">% of Wave</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(data.model_tokens).map(([model, tokens]) => {
                                const total =
                                  data.tokens_in + data.tokens_out;
                                const percent =
                                  total > 0
                                    ? ((tokens / total) * 100).toFixed(1)
                                    : '0';
                                return (
                                  <tr key={model}>
                                    <td>{model.replace('claude-', '')}</td>
                                    <td>{formatTokens(tokens)}</td>
                                    <td className="col-percent">{percent}%</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="breakdown-section">
        <h4>Cost per Agent Type</h4>
        {agents.length === 0 ? (
          <div className="breakdown-empty">
            <p>No agent-level cost data available in ledger</p>
          </div>
        ) : (
          <table className="breakdown-table">
            <thead>
              <tr>
                <th className="col-agent">Agent Type</th>
                <th className="col-runs">Runs</th>
                <th className="col-tokens">Tokens</th>
                <th className="col-cost">Cost</th>
                <th className="col-action" />
              </tr>
            </thead>
            <tbody>
              {agents.map(([agent, data]) => (
                <tbody key={agent}>
                  <tr className="breakdown-row" onClick={() => toggleAgent(agent)}>
                    <td className="col-agent">{agent}</td>
                    <td className="col-runs">{data.runs}</td>
                    <td className="col-tokens">
                      {formatTokens(data.tokens_in + data.tokens_out)}
                    </td>
                    <td className="col-cost">{formatCost(data.cost)}</td>
                    <td className="col-action">
                      <button
                        className="expand-toggle"
                        aria-label={expandedAgents[agent] ? 'Collapse' : 'Expand'}
                        aria-expanded={expandedAgents[agent] === true}
                      >
                        {expandedAgents[agent] ? '▼' : '▶'}
                      </button>
                    </td>
                  </tr>
                  {expandedAgents[agent] && (
                    <tr className="breakdown-detail">
                      <td colSpan={5}>
                        <div className="detail-content">
                          <div className="detail-stats">
                            <div className="stat">
                              <span className="stat-label">Tokens In:</span>
                              <span className="stat-value">
                                {formatTokens(data.tokens_in)}
                              </span>
                            </div>
                            <div className="stat">
                              <span className="stat-label">Tokens Out:</span>
                              <span className="stat-value">
                                {formatTokens(data.tokens_out)}
                              </span>
                            </div>
                            <div className="stat">
                              <span className="stat-label">Verdicts:</span>
                              <span className="stat-value">
                                OK {data.verdicts.OK} / Failed {data.verdicts.FAILED} / Empty{' '}
                                {data.verdicts.EMPTY} / Hung {data.verdicts.HUNG}
                              </span>
                            </div>
                          </div>
                          <h5>Model Breakdown</h5>
                          <table className="detail-table">
                            <thead>
                              <tr>
                                <th>Model</th>
                                <th>Tokens</th>
                                <th className="col-percent">% of Agent</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(data.model_tokens).map(([model, tokens]) => {
                                const total =
                                  data.tokens_in + data.tokens_out;
                                const percent =
                                  total > 0
                                    ? ((tokens / total) * 100).toFixed(1)
                                    : '0';
                                return (
                                  <tr key={model}>
                                    <td>{model.replace('claude-', '')}</td>
                                    <td>{formatTokens(tokens)}</td>
                                    <td className="col-percent">{percent}%</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
