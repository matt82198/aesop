/**
 * BenchmarkPanel component — displays benchmark results from /api/bench.
 * Shows a results table with model, accuracy, tokens, latency, cost, and timestamp.
 * Also displays a comparison view with side-by-side model stats.
 */

import { useEffect, useState } from 'react';
import { fetchApi } from '../lib/api';
import type { BenchResult, BenchComparison } from '../lib/types';
import { formatTimestamp } from '../lib/format';
import './BenchmarkPanel.css';

interface BenchPanelState {
  results: BenchResult[];
  comparison: Record<string, BenchResult>;
  loading: boolean;
  error?: string;
}

export function BenchmarkPanel() {
  const [state, setState] = useState<BenchPanelState>({
    results: [],
    comparison: {},
    loading: true,
  });

  useEffect(() => {
    const loadBenchData = async () => {
      try {
        const [resultsData, comparisonData] = await Promise.all([
          fetchApi<{ results: BenchResult[] }>('/api/bench'),
          fetchApi<{ comparison: Record<string, BenchResult> }>('/api/bench/compare'),
        ]);

        setState({
          results: resultsData?.results || [],
          comparison: comparisonData?.comparison || {},
          loading: false,
        });
      } catch (err) {
        setState((prev) => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err.message : 'Failed to load benchmark data',
        }));
      }
    };

    loadBenchData();
  }, []);

  if (state.loading) {
    return <div className="benchmark-panel">Loading benchmark data...</div>;
  }

  if (state.error) {
    return (
      <div className="benchmark-panel">
        <div className="error-message">Error: {state.error}</div>
      </div>
    );
  }

  if (state.results.length === 0) {
    return (
      <div className="benchmark-panel">
        <p>No benchmark results available yet.</p>
      </div>
    );
  }

  return (
    <div className="benchmark-panel">
      <div className="benchmark-section">
        <h3>Benchmark Results</h3>
        <div className="benchmark-table-wrapper">
          <table className="benchmark-table">
            <caption>Recent benchmark run results</caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col" className="col-numeric">
                  Accuracy
                </th>
                <th scope="col" className="col-numeric">
                  Tokens
                </th>
                <th scope="col" className="col-numeric">
                  Latency (ms)
                </th>
                <th scope="col" className="col-numeric">
                  Cost Est.
                </th>
                <th scope="col">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {state.results.map((result, idx) => (
                <tr key={idx}>
                  <td className="model-name">{result.model}</td>
                  <td className="col-numeric">
                    {((result.accuracy || 0) * 100).toFixed(1)}%
                  </td>
                  <td className="col-numeric">
                    {result.total_tokens?.toLocaleString() || '—'}
                  </td>
                  <td className="col-numeric">
                    {result.avg_latency_ms?.toFixed(1) || '—'}
                  </td>
                  <td className="col-numeric">
                    ${(result.cost_estimate || 0).toFixed(4)}
                  </td>
                  <td className="timestamp">
                    {result.timestamp
                      ? formatTimestamp(new Date(result.timestamp * 1000).toISOString())
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {Object.keys(state.comparison).length > 0 && (
        <div className="benchmark-section">
          <h3>Model Comparison</h3>
          <div className="comparison-grid">
            {Object.entries(state.comparison).map(([modelName, stats]) => (
              <div key={modelName} className="model-card">
                <h4>{modelName}</h4>
                <dl className="model-stats">
                  <dt>Accuracy</dt>
                  <dd>{((stats.accuracy || 0) * 100).toFixed(1)}%</dd>

                  {stats.total_tokens && (
                    <>
                      <dt>Total Tokens</dt>
                      <dd>{stats.total_tokens.toLocaleString()}</dd>
                    </>
                  )}

                  {stats.avg_latency_ms && (
                    <>
                      <dt>Avg Latency</dt>
                      <dd>{stats.avg_latency_ms.toFixed(1)} ms</dd>
                    </>
                  )}

                  {stats.cost_estimate && (
                    <>
                      <dt>Est. Cost</dt>
                      <dd>${stats.cost_estimate.toFixed(4)}</dd>
                    </>
                  )}

                  {stats.timestamp && (
                    <>
                      <dt>Last Run</dt>
                      <dd>
                        {formatTimestamp(new Date(stats.timestamp * 1000).toISOString())}
                      </dd>
                    </>
                  )}
                </dl>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
