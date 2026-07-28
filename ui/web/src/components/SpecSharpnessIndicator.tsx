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
import { fetchAPI } from '../lib/api';
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
        const result = await fetchAPI(`/api/quality/spec-sharpness?agent=${encodeURIComponent(agentId)}`);
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
      >
        {data.level[0]}
      </button>

      {showDetails && (
        <div className="spec-sharpness-detail" data-testid="spec-sharpness-detail">
          <div className="detail-header">
            <h4>Spec Sharpness: {data.level}</h4>
            <span className="score">{data.score}/100</span>
          </div>

          <div className="signals">
            <div className="signal">
              <label>Directives:</label>
              <span>{data.signals.directive_count}</span>
            </div>
            <div className="signal">
              <label>Acceptance Criteria:</label>
              <span>{data.signals.has_acceptance_criteria ? 'Yes' : 'No'}</span>
            </div>
            <div className="signal">
              <label>File Specificity:</label>
              <meter
                value={data.signals.file_specificity}
                min="0"
                max="1"
                low="0.33"
                high="0.66"
                className="meter"
              />
            </div>
            <div className="signal">
              <label>Structured Content:</label>
              <meter
                value={data.signals.structured_content_ratio}
                min="0"
                max="1"
                low="0.33"
                high="0.66"
                className="meter"
              />
            </div>
            <div className="signal">
              <label>Emphasis Markers:</label>
              <span>{data.signals.emphasis_markers}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
