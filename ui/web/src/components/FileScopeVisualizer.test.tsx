/**
 * Test suite for FileScopeVisualizer component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FileScopeVisualizer } from './FileScopeVisualizer';

// Mock the fetchApi function
vi.mock('../lib/api', () => ({
  fetchApi: vi.fn(),
}));

import { fetchApi } from '../lib/api';

describe('FileScopeVisualizer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', () => {
    (fetchApi as any).mockImplementation(() => new Promise(() => {})); // never resolves

    render(<FileScopeVisualizer agentId="test-agent" />);
    const loading = screen.getByTestId('file-scope-loading');
    expect(loading).toBeDefined();
  });

  it('renders error state on fetch failure', async () => {
    (fetchApi as any).mockResolvedValue({ error: 'Failed to fetch' });

    render(<FileScopeVisualizer agentId="test-agent" />);

    const errorDiv = await screen.findByTestId('file-scope-error');
    expect(errorDiv).toBeDefined();
    expect(errorDiv.textContent).toContain('Error loading file scope');
  });

  it('renders file scope with data', async () => {
    const mockData = {
      intended_files: ['ui/wave_context.py', 'ui/handler.py'],
      actual_files: ['ui/wave_context.py', 'ui/serve.py'],
      coverage: 0.5,
      drift: {
        only_intended: ['ui/handler.py'],
        only_actual: ['ui/serve.py'],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const visualizer = await screen.findByTestId('file-scope-visualizer');
    expect(visualizer).toBeDefined();
  });

  it('displays coverage percentage', async () => {
    const mockData = {
      intended_files: ['file1.tsx', 'file2.tsx', 'file3.tsx', 'file4.tsx'],
      actual_files: ['file1.tsx', 'file2.tsx'],
      coverage: 0.5,
      drift: {
        only_intended: ['file3.tsx', 'file4.tsx'],
        only_actual: [],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const visualizer = await screen.findByTestId('file-scope-visualizer');
    expect(visualizer.textContent).toContain('Coverage:');
    expect(visualizer.textContent).toContain('50%');
  });

  it('shows intended files list', async () => {
    const mockData = {
      intended_files: ['ui/wave_context.py', 'ui/handler.py'],
      actual_files: [],
      coverage: 0.0,
      drift: {
        only_intended: ['ui/wave_context.py', 'ui/handler.py'],
        only_actual: [],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const intendedList = await screen.findByTestId('intended-files-list');
    expect(intendedList).toBeDefined();
    expect(intendedList.textContent).toContain('ui/wave_context.py');
    expect(intendedList.textContent).toContain('ui/handler.py');
  });

  it('shows drift analysis when present', async () => {
    const mockData = {
      intended_files: ['file1.tsx', 'file2.tsx'],
      actual_files: ['file1.tsx', 'file3.tsx'],
      coverage: 0.5,
      drift: {
        only_intended: ['file2.tsx'],
        only_actual: ['file3.tsx'],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const onlyIntended = await screen.findByTestId('drift-only-intended');
    const onlyActual = await screen.findByTestId('drift-only-actual');

    expect(onlyIntended).toBeDefined();
    expect(onlyActual).toBeDefined();
    expect(onlyIntended.textContent).toContain('file2.tsx');
    expect(onlyActual.textContent).toContain('file3.tsx');
  });

  it('renders empty state with no files', async () => {
    const mockData = {
      intended_files: [],
      actual_files: [],
      coverage: 0.0,
      drift: {
        only_intended: [],
        only_actual: [],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const visualizer = await screen.findByTestId('file-scope-visualizer');
    expect(visualizer.textContent).toContain('No file scope information');
  });

  it('handles perfect coverage', async () => {
    const mockData = {
      intended_files: ['file1.tsx', 'file2.tsx'],
      actual_files: ['file1.tsx', 'file2.tsx'],
      coverage: 1.0,
      drift: {
        only_intended: [],
        only_actual: [],
      },
    };

    (fetchApi as any).mockResolvedValue(mockData);

    render(<FileScopeVisualizer agentId="test-agent" />);

    const coverageFill = await screen.findByTestId('coverage-fill');
    expect(coverageFill).toBeDefined();
    // Coverage should be 100%
    const styleWidth = coverageFill.style.width;
    expect(styleWidth).toBe('100%');
  });
});
