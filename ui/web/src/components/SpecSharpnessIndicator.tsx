/**
 * SpecSharpnessIndicator — Spec Sharpness badge per dispatch (C1).
 *
 * Displays a color-coded badge (Low/Med/High/Excellent) indicating prompt quality
 * signals: directive count, acceptance criteria, file specificity, structured
 * content, and emphasis markers.
 *
 * Used in agent rows and agent inspector; lazy-fetches spec data on demand.
 */

import { useEffect, useState } from 'react';
import type { SpecSharpnessScore } from '../lib/types';
import { fetchApi } from '../lib/api';
import './SpecSharpnessIndicator.css';

interface SpecSharpnessIndicatorProps {
  agentId: string;
  expanded?: boolean;
}

const LEVEL_COLORS = {
  Low: '#ef4444',
  Med: '#f59e0b',
  High: '#3b82f6',
  Excellent: '#10b981'
};

export function SpecSharpnessIndicator({ agentId, expanded = false }: SpecSharpnessIndicatorProps) {
  const [data, setData] = useState<SpecSharpnessScore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(expanded);

  useEffect(() => {
    if (!agentId) return;

    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchApi(`/api/quality/spec-sharpness?agent=${encodeURIComponent(agentId)}`);
        if (result.error) {
          setError(result.error);
        } else {
          setData(result as SpecSharpnessScore);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch spec sharpness');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [agentId]);

  if (error) {
    return (
      <div
        className="spec-sharpness-indicator error"
        title={`Spec Sharpness: ${error}`}
        data-testid="spec-sharpness-error"
      >
        ?
      </div>
    );
  }

  if (loading) {
    return (
      <div className="spec-sharpness-indicator loading" data-testid="spec-sharpness-loading">
        ...
      </div>
    );
  }

  if (!data) {
    return (
      <div className="spec-sharpness-indicator unknown" data-testid="spec-sharpness-unknown">
        N/A
      </div>
    );
  }

  const bgColor = LEVEL_COLORS[data.level] || '#6b7280';

  return (
    <div className="spec-sharpness-container">
      <button
        className="spec-sharpness-indicator"
        style={{ backgroundColor: bgColor }}
        onClick={() => setShowDetails(!showDetails)}
        title={`Spec Sharpness: ${data.level} (${data.score}/100)`}
        data-testid="spec-sharpness-badge"
        aria-pressed={showDetails}
        aria-label={`Spec Sharpness: ${data.level} (${data.score}/100)`}
      >
        {data.level[0]}
      </button>

      {showDetails && (
        <div
          className="spec-sharpness-detail"
          data-testid="spec-sharpness-detail"
          role="region"
          aria-label="Spec sharpness details"
        >
          <div className="detail-header">
            <h4>Spec Sharpness: {data.level}</h4>
            <span className="score" aria-label={`Score: ${data.score} out of 100`}>
              {data.score}/100
            </span>
          </div>

          <div className="signals">
            <div className="signal">
              <label htmlFor={`directive-count-${agentId}`}>Directives:</label>
              <span id={`directive-count-${agentId}`}>{data.signals.directive_count}</span>
            </div>
            <div className="signal">
              <label htmlFor={`acceptance-criteria-${agentId}`}>Acceptance Criteria:</label>
              <span id={`acceptance-criteria-${agentId}`}>
                {data.signals.has_acceptance_criteria ? 'Yes' : 'No'}
              </span>
            </div>
            <div className="signal">
              <label htmlFor={`file-specificity-${agentId}`}>File Specificity:</label>
              <meter
                id={`file-specificity-${agentId}`}
                value={data.signals.file_specificity}
                min={0}
                max={1}
                low={0.33}
                high={0.66}
                className="meter"
              />
            </div>
            <div className="signal">
              <label htmlFor={`structured-content-${agentId}`}>Structured Content:</label>
              <meter
                id={`structured-content-${agentId}`}
                value={data.signals.structured_content_ratio}
                min={0}
                max={1}
                low={0.33}
                high={0.66}
                className="meter"
              />
            </div>
            <div className="signal">
              <label htmlFor={`emphasis-markers-${agentId}`}>Emphasis Markers:</label>
              <span id={`emphasis-markers-${agentId}`}>{data.signals.emphasis_markers}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
