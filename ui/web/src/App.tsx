/**
 * App shell — sticky HealthHeader slot + hash-tab nav + view slots (D4).
 *
 * U1 renders placeholder views; U4–U7 replace the placeholders with real
 * components. The header slot, nav, theme toggle, and SSE plumbing are the
 * stable shell contract.
 *
 * A11y baseline (D5): all interactive elements are real <button>/<a>,
 * nav uses aria-current="page", the SSE status is a role="status" live region.
 */
import { useCallback, useEffect, useState } from 'react';
import { useHashRoute, type Route } from './lib/useHashRoute';
import { useSSE } from './lib/useSSE';
import { HealthHeader } from './components/HealthHeader';
import { Overview } from './views/Overview';
import { Work } from './views/Work';
import Activity from './views/Activity';
import { Cost } from './views/Cost';
import { WavePRBoard } from './views/WavePRBoard';
import { CostSummaryDrawer } from './components/CostSummaryDrawer';
import { QueuePanel } from './components/QueuePanel';
import type { QueuePanelData } from './lib/types';

const THEME_STORAGE_KEY = 'aesop-theme';
type Theme = 'light' | 'dark' | null; // null = follow OS preference

function readStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === null) {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', theme);
  }
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      if (theme === null) {
        localStorage.removeItem(THEME_STORAGE_KEY);
      } else {
        localStorage.setItem(THEME_STORAGE_KEY, theme);
      }
    } catch {
      // localStorage unavailable (private mode) — theme still applies for this session
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((prev) => {
      const osPrefersDark =
        typeof matchMedia !== 'undefined' &&
        matchMedia('(prefers-color-scheme: dark)').matches;
      const effective = prev ?? (osPrefersDark ? 'dark' : 'light');
      return effective === 'dark' ? 'light' : 'dark';
    });
  }, []);

  return { theme, toggle };
}

const NAV_ITEMS: Array<{ hash: Route; label: string }> = [
  { hash: '#/', label: 'Overview' },
  { hash: '#/work', label: 'Work' },
  { hash: '#/activity', label: 'Activity' },
  { hash: '#/cost', label: 'Cost' },
  { hash: '#/prs', label: 'PR Board' },
];

export default function App() {
  const route = useHashRoute();
  const sseState = useSSE();
  const { toggle } = useTheme();
  const [dataTimestamp, setDataTimestamp] = useState<number | null>(null);
  const [now, setNow] = useState<number>(Date.now());
  const [queue, setQueue] = useState<QueuePanelData | null>(null);

  const connection = sseState.connectionStatus;

  // Fetch queue data periodically (~5s)
  useEffect(() => {
    const fetchQueue = async () => {
      try {
        const response = await fetch('/api/queue');
        if (response.ok) {
          const data = await response.json();
          setQueue(data);
        }
      } catch (err) {
        // Silently fail — queue is optional data
      }
    };

    fetchQueue();
    const interval = setInterval(fetchQueue, 5000);
    return () => clearInterval(interval);
  }, []);

  // Wall-clock ticker (~5s) for staleness re-evaluation without SSE traffic
  useEffect(() => {
    const interval = setInterval(() => {
      setNow(Date.now());
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Update timestamp whenever any SSE section updates
  useEffect(() => {
    if (sseState.data || sseState.agents || sseState.tracker || sseState.cost) {
      setDataTimestamp(Date.now());
    }
  }, [sseState.data, sseState.agents, sseState.tracker, sseState.cost]);

  const handleRefresh = useCallback(() => {
    window.location.reload();
  }, []);

  return (
    <>
      <HealthHeader
        watchdog={sseState.data?.watchdog ?? null}
        monitor={sseState.data?.monitor ?? null}
        orchestrator={sseState.status ?? null}
        agents={sseState.agents ?? null}
        alerts={sseState.data?.alerts ?? null}
        cost={sseState.cost ?? null}
        connectionStatus={connection}
        dataTimestamp={dataTimestamp}
        heartbeatTimestamp={sseState.lastHeartbeat}
        now={now}
        onThemeToggle={toggle}
        onRefresh={handleRefresh}
      />
      <CostSummaryDrawer cost={sseState.cost ?? null} connectionStatus={connection} />
      <QueuePanel queue={queue} connectionStatus={connection} />
      <nav className="app-nav" aria-label="Views">
        {NAV_ITEMS.map(({ hash, label }) => (
          <a key={hash} href={hash} aria-current={route === hash ? 'page' : undefined} className="app-nav__link">
            {label}
          </a>
        ))}
      </nav>
      <main className="app-main">
        {route === '#/' && (
          <Overview
            agents={sseState.agents ?? null}
            alerts={sseState.data?.alerts ?? null}
            events={sseState.data?.events ?? null}
            repos={sseState.data?.repos ?? null}
          />
        )}
        {route === '#/work' && (
          <Work tracker={sseState.tracker ?? null} backlog={sseState.backlog ?? null} />
        )}
        {route === '#/activity' && <Activity state={sseState} />}
        {route === '#/cost' && (
          <Cost cost={sseState.cost} connectionStatus={connection} onRetry={handleRefresh} />
        )}
        {route === '#/prs' && <WavePRBoard />}
      </main>
    </>
  );
}
