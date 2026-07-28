/**
 * FileScopeVisualizer — File scope visualization (C2).
 *
 * Shows intended vs actual files touched by a dispatch. Visualizes scope
 * coverage as a progress bar and highlights drift (files only in intended,
 * files only in actual).
 *
 * Used in agent inspector drawer; lazy-fetches file scope data on demand.
 */

import { useEffect, useState } from 'react';
import type { FileScopeData } from '../lib/types';
import { fetchAPI } from '../lib/api';
import './FileScopeVisualizer.css';

interface FileScopeVisualizerProps {
  agentId: string;
}

export function FileScopeVisualizer({ agentId }: FileScopeVisualizerProps) {
  const [data, setData] = useState<FileScopeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId) return;

    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchAPI(`/api/context/files?agent=${encodeURIComponent(agentId)}`);
        if (result.error) {
          setError(result.error);
        } else {
          setData(result as FileScopeData);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch file scope');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [agentId]);

  if (error) {
    return (
      <div className="file-scope-error" data-testid="file-scope-error">
        <p>Error loading file scope: {error}</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="file-scope-loading" data-testid="file-scope-loading">
        <p>Loading file scope...</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="file-scope-empty" data-testid="file-scope-empty">
        <p>No file scope data available</p>
      </div>
    );
  }

  const coveragePercent = Math.round(data.coverage * 100);
  const intentedCount = data.intended_files.length;
  const actualCount = data.actual_files.length;
  const driftOnlyIntended = data.drift.only_intended.length;
  const driftOnlyActual = data.drift.only_actual.length;

  return (
    <div
      className="file-scope-visualizer"
      data-testid="file-scope-visualizer"
      role="region"
      aria-label="File scope analysis"
    >
      <div className="scope-header">
        <h3>File Scope Analysis</h3>
        <div className="coverage-badge" aria-label={`Coverage: ${coveragePercent}%`}>
          <span className="label">Coverage:</span>
          <span className="percent">{coveragePercent}%</span>
        </div>
      </div>

      <div className="coverage-bar" role="progressbar" aria-valuenow={coveragePercent} aria-valuemin={0} aria-valuemax={100}>
        <div
          className="coverage-fill"
          style={{ width: `${coveragePercent}%` }}
          data-testid="coverage-fill"
        />
      </div>

      <div className="file-counts">
        <div className="count-item">
          <label htmlFor="intended-count">Intended Files:</label>
          <span className="count" id="intended-count">
            {intentedCount}
          </span>
        </div>
        <div className="count-item">
          <label htmlFor="actual-count">Actual Files:</label>
          <span className="count" id="actual-count">
            {actualCount}
          </span>
        </div>
      </div>

      {/* Intended files section */}
      {intentedCount > 0 && (
        <div className="file-section">
          <h4>Intended Files ({intentedCount})</h4>
          <ul className="file-list" data-testid="intended-files-list">
            {data.intended_files.map((file, idx) => (
              <li key={idx} className="file-item">
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Actual files section */}
      {actualCount > 0 && (
        <div className="file-section">
          <h4>Actual Files ({actualCount})</h4>
          <ul className="file-list" data-testid="actual-files-list">
            {data.actual_files.map((file, idx) => (
              <li key={idx} className="file-item">
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Drift analysis */}
      {(driftOnlyIntended > 0 || driftOnlyActual > 0) && (
        <div className="drift-section">
          <h4>Scope Drift</h4>

          {driftOnlyIntended > 0 && (
            <div className="drift-item warning">
              <label>Only in Intended ({driftOnlyIntended}):</label>
              <ul className="drift-list" data-testid="drift-only-intended">
                {data.drift.only_intended.map((file, idx) => (
                  <li key={idx}>
                    <code>{file}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {driftOnlyActual > 0 && (
            <div className="drift-item info">
              <label>Only in Actual ({driftOnlyActual}):</label>
              <ul className="drift-list" data-testid="drift-only-actual">
                {data.drift.only_actual.map((file, idx) => (
                  <li key={idx}>
                    <code>{file}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Empty states */}
      {intentedCount === 0 && actualCount === 0 && (
        <div className="file-section empty">
          <p>No file scope information available</p>
        </div>
      )}
    </div>
  );
}
