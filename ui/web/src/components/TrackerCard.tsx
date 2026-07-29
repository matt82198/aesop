/**
 * TrackerCard — individual tracker item card.
 * Renders priority chip, tags, expandable details (notes, timestamps, sanitized pr_link).
 * Actions: Claim (move to in-progress), Done (move to done), Archive.
 * Errors announced via aria-live.
 */

import { useState } from 'react';
import { TESTIDS } from '../test/fixtures';
import type { TrackerItem } from '../lib/types';
import { updateTrackerItem } from '../lib/api';
import { sanitizeUrl } from '../lib/sanitizeUrl';
import { TrackerEditACModal } from './TrackerEditACModal';

interface TrackerCardProps {
  item: TrackerItem;
  onUpdate?: (updated: TrackerItem) => void;
}

const PRIORITY_STYLES: Record<string, string> = {
  P0: '--color-status-error',
  P1: '--color-status-warn',
  P2: '--color-status-info',
};

export function TrackerCard({ item, onUpdate }: TrackerCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showACModal, setShowACModal] = useState(false);

  async function handleAction(action: 'claim' | 'done' | 'archive') {
    setLoading(true);
    setError(null);
    try {
      const updates: Record<string, string> = {};
      if (action === 'claim') {
        updates.lane = 'in-progress';
        updates.status = 'in-progress';
      } else if (action === 'done') {
        updates.lane = 'done';
        updates.status = 'done';
      } else if (action === 'archive') {
        updates.status = 'archived';
        updates.lane = 'done';
      }
      const updated = await updateTrackerItem(item.id, updates);
      onUpdate?.(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setLoading(false);
    }
  }

  const priorityColor = PRIORITY_STYLES[item.priority] || '--color-status-neutral';
  const sanitized = sanitizeUrl(item.pr_link);

  return (
    <div className="tracker-card" data-testid={TESTIDS.trackerCard}>
      <div className="tracker-card-header">
        <div className="tracker-card-title-row">
          <span
            className="priority-chip"
            style={{ backgroundColor: `var(${priorityColor})` }}
          >
            {item.priority}
          </span>
          <h3 className="tracker-card-title">{item.title}</h3>
        </div>
        <button
          type="button"
          className="expand-button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} details for ${item.title}`}
        >
          {expanded ? '▼' : '▶'}
        </button>
      </div>

      {item.tags.length > 0 && (
        <div className="tracker-card-tags">
          {item.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      )}

      {expanded && (
        <div className="tracker-card-details">
          {item.notes && (
            <div className="detail-section">
              <label className="detail-label">Notes</label>
              <p>{item.notes}</p>
            </div>
          )}

          {item.acceptanceCriteria && item.acceptanceCriteria.length > 0 && (
            <div className="detail-section">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <label className="detail-label">Acceptance Criteria</label>
                <button
                  type="button"
                  onClick={() => setShowACModal(true)}
                  style={{ padding: '0.25rem 0.75rem', fontSize: '0.875rem' }}
                  disabled={loading}
                >
                  Edit
                </button>
              </div>
              <ul style={{ marginTop: '0.5rem', listStyle: 'none', paddingLeft: 0 }}>
                {item.acceptanceCriteria.map((ac, idx) => (
                  <li key={idx} style={{ marginBottom: '0.5rem', padding: '0.5rem', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px' }}>
                    <strong>{ac.statement}</strong>
                    <br />
                    <small style={{ color: 'var(--text-secondary)' }}>{ac.verifiable_by}</small>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(!item.acceptanceCriteria || item.acceptanceCriteria.length === 0) && (
            <div className="detail-section">
              <button
                type="button"
                onClick={() => setShowACModal(true)}
                style={{ padding: '0.5rem 1rem', fontSize: '0.875rem', marginBottom: '0.5rem' }}
                disabled={loading}
              >
                + Add Acceptance Criteria
              </button>
            </div>
          )}

          <div className="detail-section">
            <label className="detail-label">Created</label>
            <time>{new Date(item.created_at).toLocaleString()}</time>
          </div>

          {item.completed_at && (
            <div className="detail-section">
              <label className="detail-label">Completed</label>
              <time>{new Date(item.completed_at).toLocaleString()}</time>
            </div>
          )}

          {item.pr_link && (
            <div className="detail-section">
              <label className="detail-label">PR Link</label>
              {sanitized ? (
                <a href={sanitized} target="_blank" rel="noopener noreferrer">
                  {item.pr_link}
                </a>
              ) : (
                <code className="pr-link-inert">{item.pr_link}</code>
              )}
            </div>
          )}
        </div>
      )}

      <div className="tracker-card-actions">
        <button
          type="button"
          disabled={loading || item.status === 'in-progress'}
          onClick={() => handleAction('claim')}
          aria-label={`Claim: ${item.title}`}
        >
          Claim
        </button>
        <button
          type="button"
          disabled={loading || item.status === 'done'}
          onClick={() => handleAction('done')}
          aria-label={`Mark done: ${item.title}`}
        >
          Done
        </button>
        <button
          type="button"
          disabled={loading || item.status === 'archived'}
          onClick={() => handleAction('archive')}
          aria-label={`Archive: ${item.title}`}
        >
          Archive
        </button>
      </div>

      {error && (
        <div className="card-error" role="alert" aria-live="assertive">
          {error}
        </div>
      )}

      {showACModal && (
        <TrackerEditACModal
          item={item}
          onClose={() => setShowACModal(false)}
          onSuccess={() => {
            // Refresh is handled by SSE, just close the modal
          }}
        />
      )}
    </div>
  );
}
