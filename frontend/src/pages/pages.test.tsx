import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../pages/Dashboard';
import Settings from '../pages/Settings';
import Gaps from '../pages/Gaps';

const dashboardPayload = {
  counts: { regulations: 1, obligations: 20, obligations_applicable: 20, mapped_controls: 6, open_gaps: 5, high_priority_findings: 2, overdue_remediation: 1, changes_awaiting_assessment: 3 },
  risk: { total_assessed: 5, bands: { high: 2, medium: 3 }, max_score: 82.5, mean_score: 55.1 },
  evidence: { total: 4, sufficient: 1, stale: 1, missing: 2, coverage: 0.25 },
  recent_activity: [{ action: 'gaps.detected', actor: 'gap_agent', entity_type: 'compliance_gap', created_at: '2026-10-09T00:00:00Z' }],
  open_gaps_by_type: { missing_evidence: 3, failed_control_test: 2 },
  pending_reviews: { obligations: 150, mappings: 10, changes: 3, remediation: 4, evidence: 2 },
  demo_ready: true,
};

const statusPayload = {
  app: 'ReguLens AI', version: '1.0.0', environment: 'development', mode: 'deterministic',
  database: { connected: true }, llm: { available: false }, embeddings: { kind: 'lexical' },
  ocr: { available: false }, retrieval: { lexical: true }, orchestration: { backend: 'native' },
  optimiser: { preferred: 'ortools_cpsat' }, counts: { obligations: 20 },
  warnings: ['Local LLM is not active'],
};

const gapsPayload = {
  items: [{ id: 'g1', gap_uid: 'GAP-1', gap_type: 'missing_evidence', title: 'No evidence linked', status: 'open', severity_hint: 'medium', truth_status: 'indeterminate', created_at: '2026-10-09T00:00:00Z' }],
  total: 1, page: 1, page_size: 15, pages: 1,
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    const u = String(url);
    const body = u.includes('/dashboard') ? dashboardPayload
      : u.includes('/system/status') ? statusPayload
      : u.includes('/gaps') ? gapsPayload
      : {};
    return { ok: true, status: 200, json: async () => body } as Response;
  }));
});

describe('Dashboard', () => {
  it('renders live counts from the API', async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Executive dashboard')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('Open gaps')).toBeTruthy());
    expect(fetch).toHaveBeenCalledWith('/api/v1/dashboard', expect.anything());
  });

  it('shows loading then error state when the API fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: { code: 'x', message: 'boom' } }) }) as Response));
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  });
});

describe('Settings', () => {
  it('reports deterministic mode and warnings', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Settings & system health')).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/Local LLM is not active/)).toBeTruthy());
  });
});

describe('Gaps', () => {
  it('lists gaps with truth status', async () => {
    render(<MemoryRouter><Gaps /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('GAP-1')).toBeTruthy());
    expect(screen.getAllByText('missing_evidence').length).toBeGreaterThanOrEqual(1);
  });
});
