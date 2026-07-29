/**
 * Work view — tracker kanban board + backlog panel + form.
 * Binds TrackerBoard, TrackerCard, TrackerForm, BacklogPanel.
 * Reads tracker + backlog from SSE (via App props), allows mutations.
 */

import { useEffect, useState } from 'react';
import { TrackerBoard } from '../components/TrackerBoard';
import { TrackerForm } from '../components/TrackerForm';
import { BacklogPanel } from '../components/BacklogPanel';
import { WaveTelemetryCost } from '../components/WaveTelemetryCost';
import { WaveQualityScorecards } from '../components/WaveQualityScorecards';
import { TESTIDS } from '../test/fixtures';
import type { TrackerItem, AuditBacklog } from '../lib/types';
import type { SSEState } from '../lib/useSSE';
import '../styles/work.css';

interface WorkProps {
  tracker: SSEState['tracker'] | null;
  backlog: SSEState['backlog'] | null;
}

export function Work({ tracker, backlog: backlogProp }: WorkProps) {
  const [trackerItems, setTrackerItems] = useState<TrackerItem[]>([]);
  const [backlog, setBacklog] = useState<AuditBacklog | null>(null);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    if (tracker?.items !== undefined && tracker?.items !== null) {
      setTrackerItems(tracker.items);
    } else if (tracker === null) {
      // Treat null items as empty array
      setTrackerItems([]);
    }
  }, [tracker]);

  useEffect(() => {
    if (backlogProp) {
      setBacklog(backlogProp);
    }
  }, [backlogProp]);

  const handleItemUpdate = (updated: TrackerItem) => {
    setTrackerItems((prev) =>
      prev.map((item) => (item.id === updated.id ? updated : item))
    );
  };

  const handleFormSuccess = () => {
    // Delay form close to allow success message to be visible
    setTimeout(() => {
      setShowForm(false);
    }, 2000);
    // Tracker board will update via SSE
  };

  return (
    <section className="work-view" data-testid={TESTIDS.viewWork} aria-label="Work view">
      <div className="work-container">
        <div className="work-board">
          <div className="board-header">
            <h2>Tracker Kanban</h2>
            <button
              type="button"
              className="add-item-button"
              onClick={() => setShowForm(!showForm)}
              aria-expanded={showForm}
            >
              {showForm ? 'Cancel' : '+ Add Item'}
            </button>
          </div>

          {showForm && (
            <div className="form-container">
              <TrackerForm onSuccess={handleFormSuccess} />
            </div>
          )}

          <TrackerBoard items={trackerItems} onUpdate={handleItemUpdate} />
        </div>

        <aside className="work-sidebar">
          <WaveTelemetryCost />
          <WaveQualityScorecards />
          <BacklogPanel backlog={backlog} />
        </aside>
      </div>
    </section>
  );
}
