/**
 * TrackerEditACModal — modal to view and edit acceptanceCriteria on a tracker item.
 * Displayed as part of TrackerCard when AC edit is triggered.
 */

import { useState } from 'react';
import { TESTIDS } from '../test/fixtures';
import { updateTrackerItem } from '../lib/api';
import type { TrackerItem, AcceptanceCriterion } from '../lib/types';

interface TrackerEditACModalProps {
  item: TrackerItem;
  onClose: () => void;
  onSuccess?: () => void;
}

export function TrackerEditACModal({ item, onClose, onSuccess }: TrackerEditACModalProps) {
  const [acList, setAcList] = useState<AcceptanceCriterion[]>(item.acceptanceCriteria ?? []);
  const [acStatement, setAcStatement] = useState('');
  const [acVerifiable, setAcVerifiable] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function addAcceptanceCriterion() {
    if (!acStatement.trim() || !acVerifiable.trim()) {
      setError('Both statement and verifiable_by are required for AC');
      return;
    }
    setAcList([...acList, {
      statement: acStatement.trim(),
      verifiable_by: acVerifiable.trim(),
    }]);
    setAcStatement('');
    setAcVerifiable('');
    setError(null);
  }

  function removeAcceptanceCriterion(index: number) {
    setAcList(acList.filter((_, i) => i !== index));
  }

  async function handleSave() {
    setLoading(true);
    try {
      await updateTrackerItem(item.id, {
        acceptanceCriteria: acList.length > 0 ? acList : undefined,
      });
      setSuccess(true);
      onSuccess?.();
      setTimeout(() => onClose(), 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update item');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-color)',
          borderRadius: '8px',
          padding: '2rem',
          maxWidth: '600px',
          maxHeight: '80vh',
          overflowY: 'auto',
          boxShadow: '0 10px 40px rgba(0, 0, 0, 0.3)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>Edit Acceptance Criteria: {item.title}</h2>

        <div style={{ marginBottom: '1.5rem', padding: '1rem', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px' }}>
          <div style={{ marginBottom: '1rem' }}>
            <label htmlFor="ac-statement">Statement</label>
            <input
              id="ac-statement"
              type="text"
              value={acStatement}
              onChange={(e) => setAcStatement(e.target.value)}
              placeholder="e.g., All tests pass"
              disabled={loading}
              style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
            />
          </div>

          <div style={{ marginBottom: '1rem' }}>
            <label htmlFor="ac-verifiable">Verifiable By</label>
            <input
              id="ac-verifiable"
              type="text"
              value={acVerifiable}
              onChange={(e) => setAcVerifiable(e.target.value)}
              placeholder="e.g., pytest tests/test_feature.py"
              disabled={loading}
              style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
            />
          </div>

          <button
            type="button"
            onClick={addAcceptanceCriterion}
            disabled={loading}
            style={{ padding: '0.5rem 1rem' }}
            data-testid={TESTIDS.trackerFormAddAC}
          >
            Add Criterion
          </button>
        </div>

        {acList.length > 0 && (
          <div style={{ marginBottom: '1.5rem' }}>
            <strong>Criteria:</strong>
            <ul style={{ marginTop: '0.5rem', listStyle: 'none', paddingLeft: 0 }}>
              {acList.map((ac, idx) => (
                <li
                  key={idx}
                  style={{
                    marginBottom: '0.5rem',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    padding: '0.5rem',
                    backgroundColor: 'var(--bg-primary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                  }}
                >
                  <span>
                    <strong>{ac.statement}</strong>
                    <br />
                    <small style={{ color: 'var(--text-secondary)' }}>{ac.verifiable_by}</small>
                  </span>
                  <button
                    type="button"
                    onClick={() => removeAcceptanceCriterion(idx)}
                    disabled={loading}
                    style={{
                      marginLeft: '1rem',
                      padding: '0.25rem 0.5rem',
                      backgroundColor: 'var(--error-bg)',
                      color: 'var(--error-text)',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                    }}
                    data-testid={`${TESTIDS.trackerFormRemoveAC}-${idx}`}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && (
          <div
            style={{
              marginBottom: '1rem',
              padding: '0.75rem',
              backgroundColor: 'var(--error-bg)',
              color: 'var(--error-text)',
              borderRadius: '4px',
            }}
            role="alert"
            aria-live="assertive"
          >
            {error}
          </div>
        )}

        {success && (
          <div
            style={{
              marginBottom: '1rem',
              padding: '0.75rem',
              backgroundColor: 'var(--success-bg)',
              color: 'var(--success-text)',
              borderRadius: '4px',
            }}
            role="status"
            aria-live="polite"
          >
            Acceptance criteria updated successfully!
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={loading}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: 'var(--primary)',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
            data-testid={TESTIDS.trackerEditAC}
          >
            {loading ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  );
}
