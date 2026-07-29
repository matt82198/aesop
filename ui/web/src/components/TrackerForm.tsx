/**
 * TrackerForm — form to create new tracker items.
 * Labeled inputs for title, priority, tags, notes, and optional acceptanceCriteria.
 * Submit via api.ts with CSRF.
 * Validation, success/error announced via aria-live.
 */

import { useState } from 'react';
import { TESTIDS } from '../test/fixtures';
import { createTrackerItem } from '../lib/api';
import type { AcceptanceCriterion } from '../lib/types';

interface TrackerFormProps {
  onSuccess?: () => void;
}

export function TrackerForm({ onSuccess }: TrackerFormProps) {
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState('P1');
  const [tags, setTags] = useState('');
  const [notes, setNotes] = useState('');
  const [acList, setAcList] = useState<AcceptanceCriterion[]>([]);
  const [acStatement, setAcStatement] = useState('');
  const [acVerifiable, setAcVerifiable] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    // Validation
    if (!title.trim()) {
      setError('Title is required');
      return;
    }

    // If validation passes, proceed with async submit
    performSubmit();
  }

  async function performSubmit() {

    const tagArray = tags
      .split(',')
      .map((t) => t.trim())
      .filter((t) => t.length > 0);

    setLoading(true);
    try {
      await createTrackerItem({
        title: title.trim(),
        priority,
        tags: tagArray,
        notes: notes.trim() || undefined,
        acceptanceCriteria: acList.length > 0 ? acList : undefined,
      });

      setSuccess(true);
      setTitle('');
      setPriority('P1');
      setTags('');
      setNotes('');
      setAcList([]);
      setAcStatement('');
      setAcVerifiable('');
      onSuccess?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create item');
    } finally {
      setLoading(false);
    }
  }

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

  return (
    <form className="tracker-form" data-testid={TESTIDS.trackerForm} onSubmit={handleSubmit}>
      <div className="form-group">
        <label htmlFor="tracker-title">Title</label>
        <input
          id="tracker-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Enter item title"
          disabled={loading}
          required
          data-testid={TESTIDS.trackerFormTitle}
        />
      </div>

      <div className="form-group">
        <label htmlFor="tracker-priority">Priority</label>
        <select
          id="tracker-priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          disabled={loading}
        >
          <option value="P0">P0 (Critical)</option>
          <option value="P1">P1 (High)</option>
          <option value="P2">P2 (Medium)</option>
        </select>
      </div>

      <div className="form-group">
        <label htmlFor="tracker-tags">Tags (comma-separated)</label>
        <input
          id="tracker-tags"
          type="text"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="e.g., ui, wave-14, critical"
          disabled={loading}
        />
      </div>

      <div className="form-group">
        <label htmlFor="tracker-notes">Notes</label>
        <textarea
          id="tracker-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional notes"
          rows={3}
          disabled={loading}
        />
      </div>

      <div className="form-group">
        <label>Acceptance Criteria (optional)</label>
        <div style={{ marginBottom: '1rem', padding: '0.5rem', backgroundColor: '#f5f5f5', borderRadius: '4px' }}>
          <div className="form-group">
            <label htmlFor="ac-statement">Statement</label>
            <input
              id="ac-statement"
              type="text"
              value={acStatement}
              onChange={(e) => setAcStatement(e.target.value)}
              placeholder="e.g., All tests pass"
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="ac-verifiable">Verifiable By</label>
            <input
              id="ac-verifiable"
              type="text"
              value={acVerifiable}
              onChange={(e) => setAcVerifiable(e.target.value)}
              placeholder="e.g., pytest tests/test_feature.py"
              disabled={loading}
            />
          </div>

          <button
            type="button"
            onClick={addAcceptanceCriterion}
            disabled={loading}
            style={{ marginBottom: '1rem' }}
            data-testid={TESTIDS.trackerFormAddAC}
          >
            Add Criterion
          </button>

          {acList.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <strong>Added criteria:</strong>
              <ul style={{ marginTop: '0.5rem' }}>
                {acList.map((ac, idx) => (
                  <li key={idx} style={{ marginBottom: '0.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>
                      <strong>{ac.statement}</strong> - {ac.verifiable_by}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeAcceptanceCriterion(idx)}
                      disabled={loading}
                      style={{ marginLeft: '1rem', padding: '0.25rem 0.5rem' }}
                      data-testid={`${TESTIDS.trackerFormRemoveAC}-${idx}`}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <button
        type="submit"
        disabled={loading}
        data-testid={TESTIDS.trackerFormSubmit}
      >
        {loading ? 'Creating...' : 'Create Item'}
      </button>

      {error && (
        <div className="form-error" role="alert" aria-live="assertive">
          {error}
        </div>
      )}

      {success && (
        <div className="form-success" role="status" aria-live="polite">
          Item created successfully!
        </div>
      )}
    </form>
  );
}
