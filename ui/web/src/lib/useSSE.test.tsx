/**
 * useSSE lifecycle regression: a hook instance whose effect is cleaned up and
 * re-run on the SAME fiber (what StrictMode's double effect does) must open a
 * fresh EventSource instead of reusing the closed one.
 *
 * Driven through React's <Activity>: hiding it runs the effect cleanup, and
 * showing it again re-runs the effect on the same component instance (refs
 * preserved). Separate renderHook() calls do not cover this, because each call
 * gets a brand-new component instance with fresh refs.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { Activity } from 'react';
import { render, act } from '@testing-library/react';
import { useSSE } from './useSSE';

const CLOSED = 2;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  readyState = 0;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener() {}
  removeEventListener() {}

  close() {
    this.readyState = CLOSED;
  }
}

function Probe() {
  useSSE();
  return null;
}

function Host({ mode }: { mode: 'visible' | 'hidden' }) {
  return (
    <Activity mode={mode}>
      <Probe />
    </Activity>
  );
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useSSE lifecycle', () => {
  it('opens a live EventSource when the same instance is re-mounted after cleanup', () => {
    const view = render(<Host mode="visible" />);
    expect(FakeEventSource.instances.length).toBe(1);

    // Cleanup runs: the first source is closed.
    act(() => {
      view.rerender(<Host mode="hidden" />);
    });
    expect(FakeEventSource.instances[0].readyState).toBe(CLOSED);

    // Effect re-runs on the same instance: a second source must be constructed.
    act(() => {
      view.rerender(<Host mode="visible" />);
    });
    expect(FakeEventSource.instances.length).toBe(2);
    expect(FakeEventSource.instances[1].readyState).not.toBe(CLOSED);
  });

  it('opens a fresh EventSource when a new instance mounts after unmount', () => {
    const first = render(<Probe />);
    expect(FakeEventSource.instances.length).toBe(1);

    first.unmount();
    expect(FakeEventSource.instances[0].readyState).toBe(CLOSED);

    render(<Probe />);
    expect(FakeEventSource.instances.length).toBe(2);
    expect(FakeEventSource.instances[1].readyState).not.toBe(CLOSED);
  });
});
