/**
 * Wave Telemetry Progress Tile — shows current wave/phase and top blocker.
 *
 * Displays:
 * - Wave number/name (e.g., "wave-rc.2")
 * - Phase (e.g., "rc-1-published-source-available")
 * - Top blocker from AUDIT-BACKLOG.md
 *
 * Polls GET /api/wave/telemetry every ~5s to stay current during a live wave.
 * Accepts optional fetcher prop for dependency injection in tests.
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import { fetchApi as defaultFetcher } from '../lib/api';
import { TESTIDS } from '../test/fixtures';
import './WaveTelemetryProgress.css';

const POLL_INTERVAL_MS = 5000; // 5 seconds

interface WaveTelemetry {
  wave: string;
  phase: string;
  blocker: string;
  tokens_used: number;
  top_model: string;
  ok_rate: number;
  // Optional real fields from ui/wave_telemetry.py — not yet exercised by all
  // callers/tests, so kept optional rather than widening the required contract.
  tokens_burned_per_min?: number;
  cost_ceiling_exceeded?: boolean;
}

interface WaveTelemetryProgressProps {
  fetcher?: (path: string) => Promise<WaveTelemetry>;
}

export function WaveTelemetryProgress({ fetcher = defaultFetcher }: WaveTelemetryProgressProps) {
  const [telemetry, setTelemetry] = useState<WaveTelemetry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadTelemetry = useCallback(async () => {
    try {
      setError(null);
      const data = await fetcher('/api/wave/telemetry');
      setTelemetry(data);
      if (loading) {
        setLoading(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load wave telemetry');
      console.error('[WaveTelemetryProgress] Load failed:', err);
      if (loading) {
        setLoading(false);
      }
    }
  }, [fetcher, loading]);

  useEffect(() => {
    // Fetch immediately on mount
    loadTelemetry();
    // Set up polling interval
    pollTimerRef.current = setInterval(loadTelemetry, POLL_INTERVAL_MS);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [loadTelemetry]);

  if (loading) {
    return (
      <section
        className="wave-telemetry-progress"
        data-testid={TESTIDS.waveTelemetryProgress}
        aria-label="Wave progress"
      >
        <div className="wave-progress-header">
          <h3>Wave Progress</h3>
        </div>
        <div className="wave-progress-content">
          <p>Loading wave telemetry...</p>
        </div>
      </section>
    );
  }

  if (error || !telemetry) {
    return (
      <section
        className="wave-telemetry-progress"
        data-testid={TESTIDS.waveTelemetryProgress}
        aria-label="Wave progress"
      >
        <div className="wave-progress-header">
          <h3>Wave Progress</h3>
        </div>
        <div className="wave-progress-content">
          <p className="wave-progress-error">
            {error ? `Error: ${error}` : 'No wave data available'}
          </p>
        </div>
      </section>
    );
  }

  // Normalize phase for display (e.g., "rc-1-published-source-available" → "Published (rc.1)")
  const phaseDisplay = formatPhaseForDisplay(telemetry.phase);

  // ok_rate defaults to 0.0 both when no verification runs have happened yet AND
  // when they all genuinely failed — tokens_used > 0 is the real signal that
  // runs exist, so we don't paint a misleading 0% bar for "no data yet".
  const hasRuns = telemetry.tokens_used > 0;
  const passRatePct = Math.round((telemetry.ok_rate ?? 0) * 100);
  const barStatus = passRatePct >= 90 ? 'ok' : passRatePct >= 70 ? 'warn' : 'error';
  const blockerDisplay = telemetry.blocker === 'unknown' ? 'No blocker recorded' : telemetry.blocker;

  return (
    <section
      className="wave-telemetry-progress"
      data-testid={TESTIDS.waveTelemetryProgress}
      aria-label="Wave progress"
    >
      <div className="wave-progress-header">
        <h3>Wave Progress</h3>
        <div className="wave-progress-badge">{telemetry.wave}</div>
      </div>

      <div className="wave-progress-content">
        <div className="wave-progress-phase">
          <div className="phase-label">Phase</div>
          <div className="phase-value">{phaseDisplay}</div>
        </div>

        <div className="wave-progress-bar-section">
          <div className="wave-progress-bar-row">
            <span className="wave-progress-bar-label">Verification pass rate</span>
            <span className="wave-progress-bar-value">
              {hasRuns ? `${passRatePct}%` : 'n/a — no runs yet'}
            </span>
          </div>
          <div
            className="wave-progress-bar"
            role="progressbar"
            aria-valuenow={hasRuns ? passRatePct : undefined}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Verification pass rate"
          >
            <div
              className="wave-progress-bar-fill"
              data-status={barStatus}
              style={{ width: hasRuns ? `${passRatePct}%` : '0%' }}
            />
          </div>
        </div>

        <div className="wave-progress-blocker">
          <div className="blocker-label">Top Blocker</div>
          <div className="blocker-value">{blockerDisplay}</div>
        </div>

        <div className="wave-progress-meta">
          <span className="wave-progress-meta__item">
            Tokens: {telemetry.tokens_used.toLocaleString()}
          </span>
          <span className="wave-progress-meta__item">Top model: {telemetry.top_model}</span>
          {typeof telemetry.tokens_burned_per_min === 'number' && telemetry.tokens_burned_per_min > 0 && (
            <span className="wave-progress-meta__item">
              Burn: {telemetry.tokens_burned_per_min.toLocaleString()}/min
            </span>
          )}
          {telemetry.cost_ceiling_exceeded && (
            <span className="wave-progress-meta__item wave-progress-meta__item--warn">
              Cost ceiling exceeded
            </span>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Format phase string for display.
 * Examples:
 * - "rc-1-published-source-available" → "Published (rc.1)"
 * - "wave-rc.2: build" → "Build (wave-rc.2)"
 * - "unknown" → "Unknown"
 */
function formatPhaseForDisplay(phase: string): string {
  if (phase === 'unknown' || !phase) {
    return 'Unknown';
  }

  // Extract wave number if present (e.g., "rc-1", "wave-rc.2")
  const waveMatch = phase.match(/(?:wave-)?(\w+[\w.-]*)/i);
  const waveLabel = waveMatch ? ` (${waveMatch[1]})` : '';

  // Capitalize first word
  const words = phase.split(/[-_]/);
  const mainPhase = words[0].charAt(0).toUpperCase() + words[0].slice(1);

  return mainPhase + waveLabel;
}
