import React from 'react';
import { Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { get } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip, usePage } from '../components/ui';

interface ProviderInfo {
  available?: boolean;
  model?: string;
  reason?: string;
  note?: string;
  known_models?: string[];
  known_models_sample?: string[];
  ledger?: Record<string, unknown>;
}

interface Providers {
  gemini?: ProviderInfo;
  cerebras?: ProviderInfo;
  deterministic?: Record<string, unknown>;
  routing?: Record<string, string>;
  requests_made?: number;
  failed_requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cerebras_ledger?: {
    total_spend_usd?: number;
    budget_usd?: number;
    remaining_application_budget_usd?: number;
    daily_spend_usd?: number;
    daily_budget_usd?: number;
    per_call_limit_usd?: number;
    request_count?: number;
    failed_count?: number;
    total_input_tokens?: number;
    total_output_tokens?: number;
    disabled?: boolean;
    disabled_reason?: string;
    pricing_source?: string;
    balance_note?: string;
  };
  cache?: { entries?: number; hits?: number; misses?: number; hit_rate?: number };
  fallback_events?: Array<{ task?: string; from?: string; to?: string; reason?: string; at?: string }>;
  fallback_count?: number;
}

interface SysStatus {
  app: string; version: string; environment: string; mode: string;
  database: Record<string, unknown>;
  llm: Record<string, unknown>;
  embeddings: Record<string, unknown>;
  ocr: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  orchestration: Record<string, unknown>;
  optimiser: Record<string, unknown>;
  counts: Record<string, number>;
  warnings: string[];
  providers?: Providers;
  execution_modes?: Record<string, unknown>;
}

function KV({ obj }: { obj: Record<string, unknown> }) {
  return (
    <Table size="small">
      <TableBody>
        {Object.entries(obj).map(([k, v]) => (
          <TableRow key={k}>
            <TableCell sx={{ width: 220 }}>{k}</TableCell>
            <TableCell>{typeof v === 'boolean' ? <StatusChip value={v ? 'yes' : 'no'} label={v ? 'yes' : 'no'} /> : JSON.stringify(v)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function Settings() {
  const { data, error, loading, reload } = usePage(() => get<SysStatus>('/api/v1/system/status'));
  if (loading) return <Loading what="system status" />;
  if (error || !data) return <ErrorBox message={error ?? 'No data'} onRetry={reload} />;
  const p = data.providers;
  const ledger = p?.cerebras_ledger;
  return (
    <Box>
      <Typography variant="h5" gutterBottom>Settings & system health</Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        {data.app} {data.version} · environment {data.environment} · execution mode <StatusChip value={data.mode} />.
        Deterministic pipelines always work with no keys; AI-assisted tasks use hosted providers only when configured.
      </Typography>
      {data.warnings.length > 0 && (
        <Section title="Capability notices">
          {data.warnings.map((w, i) => <Typography key={i} variant="body2" gutterBottom>• {w}</Typography>)}
        </Section>
      )}
      <Section title="AI providers (Gemini + Cerebras)">
        <Table size="small">
          <TableHead><TableRow><TableCell>Provider</TableCell><TableCell>Available</TableCell><TableCell>Model</TableCell><TableCell>Detail</TableCell></TableRow></TableHead>
          <TableBody>
            <TableRow>
              <TableCell>Gemini (obligation extraction, interpretation)</TableCell>
              <TableCell><StatusChip value={p?.gemini?.available ? 'yes' : 'no'} label={p?.gemini?.available ? 'available' : 'unavailable'} /></TableCell>
              <TableCell>{p?.gemini?.model ?? '—'}</TableCell>
              <TableCell>{p?.gemini?.note ?? p?.gemini?.reason ?? 'Not configured'}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Cerebras (mapping, remediation drafts)</TableCell>
              <TableCell><StatusChip value={p?.cerebras?.available ? 'yes' : 'no'} label={p?.cerebras?.available ? 'available' : 'unavailable'} /></TableCell>
              <TableCell>{p?.cerebras?.model ?? '—'}</TableCell>
              <TableCell>{(p?.cerebras as ProviderInfo)?.note ?? p?.cerebras?.reason ?? 'Not configured'}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Deterministic (parsers, rules, OR-Tools)</TableCell>
              <TableCell><StatusChip value="yes" label="always on" /></TableCell>
              <TableCell>—</TableCell>
              <TableCell>Rule-based pipelines; AI-only tasks return review-required when providers are off.</TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Current execution mode: <StatusChip value={data.mode} /> ·
          requests {p?.requests_made ?? 0} · failed {p?.failed_requests ?? 0} ·
          tokens in/out {p?.input_tokens ?? 0}/{p?.output_tokens ?? 0} ·
          cache hit rate {((p?.cache?.hit_rate ?? 0) * 100).toFixed(1)}% ·
          fallbacks {p?.fallback_count ?? 0}.
          Each AI response labels the provider that produced it; deterministic output is never presented as AI-generated.
        </Typography>
      </Section>
      <Section title="Cerebras spend guard (application-level)">
        <Table size="small">
          <TableBody>
            <TableRow><TableCell sx={{ width: 260 }}>Estimated spend (this app, local tracking)</TableCell><TableCell>${(ledger?.total_spend_usd ?? 0).toFixed(4)} of ${Number(ledger?.budget_usd ?? 4).toFixed(2)} application budget</TableCell></TableRow>
            <TableRow><TableCell>Application budget remaining (estimate)</TableCell><TableCell>${(ledger?.remaining_application_budget_usd ?? 4).toFixed(4)}</TableCell></TableRow>
            <TableRow><TableCell>Today</TableCell><TableCell>${(ledger?.daily_spend_usd ?? 0).toFixed(4)} of ${Number(ledger?.daily_budget_usd ?? 1).toFixed(2)} daily limit</TableCell></TableRow>
            <TableRow><TableCell>Per-call limit</TableCell><TableCell>${Number(ledger?.per_call_limit_usd ?? 0.05).toFixed(4)}</TableCell></TableRow>
            <TableRow><TableCell>Calls (ok / failed)</TableCell><TableCell>{ledger?.request_count ?? 0} / {ledger?.failed_count ?? 0}</TableCell></TableRow>
            <TableRow><TableCell>Guard status</TableCell><TableCell>{ledger?.disabled ? `DISABLED — ${ledger?.disabled_reason ?? ''}` : 'active'}</TableCell></TableRow>
          </TableBody>
        </Table>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {ledger?.balance_note ?? 'Locally-tracked estimated spend only — not the account balance. The Cerebras provider dashboard is authoritative for the real balance.'}
          {' '}Pricing: {ledger?.pricing_source ?? 'official Cerebras pricing docs'}.
        </Typography>
      </Section>
      {(p?.fallback_events?.length ?? 0) > 0 && (
        <Section title="Recent provider fallbacks">
          <Table size="small">
            <TableHead><TableRow><TableCell>Task</TableCell><TableCell>From</TableCell><TableCell>To</TableCell><TableCell>Reason</TableCell><TableCell>At</TableCell></TableRow></TableHead>
            <TableBody>
              {(p?.fallback_events ?? []).map((f, i) => (
                <TableRow key={i}><TableCell>{f.task}</TableCell><TableCell>{f.from}</TableCell><TableCell>{f.to}</TableCell><TableCell>{f.reason}</TableCell><TableCell>{f.at}</TableCell></TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
      )}
      <Section title="Data handling & privacy">
        {[
          'Demo with synthetic or public regulatory documents only. Do not send real bank confidential data, customer data, credentials, or sensitive internal policies to hosted APIs without explicit authorization and a data-handling review.',
          'Uploaded files, the database, audit records, and logs stay local unless an explicitly authorized feature requires otherwise.',
          'API keys live only on the server (.env, never committed) and are never sent to the browser or written to logs.',
          'Free-tier data-use and retention terms are set by each provider and may differ from paid tiers — check the current Google and Cerebras terms before sending anything sensitive.',
        ].map((s, i) => <Typography key={i} variant="body2" gutterBottom>• {s}</Typography>)}
      </Section>
      <Section title="Database"><KV obj={data.database} /></Section>
      <Section title="Provider details (raw)"><KV obj={{ ...(data.llm ?? {}), routing: p?.routing ?? {} }} /></Section>
      <Section title="Embeddings (lexical, local)"><KV obj={data.embeddings} /></Section>
      <Section title="OCR"><KV obj={data.ocr} /></Section>
      <Section title="Retrieval"><KV obj={data.retrieval} /></Section>
      <Section title="Orchestration"><KV obj={data.orchestration} /></Section>
      <Section title="Optimiser"><KV obj={data.optimiser} /></Section>
      <Section title="Record counts"><KV obj={data.counts as Record<string, unknown>} /></Section>
      <Section title="Optional setup">
        {[
          'Gemini: set GEMINI_API_KEY and GEMINI_MODEL (e.g. gemini-2.0-flash; verify availability in Settings), keep GEMINI_ENABLED=true.',
          'Cerebras: set CEREBRAS_API_KEY and CEREBRAS_MODEL (e.g. gpt-oss-120b), keep CEREBRAS_ENABLED=true. Spend guards default to a USD 4.00 application budget with USD 1.00/day and USD 0.05/call limits.',
          'Routing: LLM_PROVIDER=gemini LLM_FALLBACK_PROVIDER=cerebras; override per task with LLM_ROUTING_OVERRIDES JSON. DETERMINISTIC_MODE=true forces local-only operation.',
          'OCR: install the Tesseract binary and the Python extras (`pip install "regulens-ai[ocr]"`), keep ENABLE_OCR=true.',
          'Database: `docker compose up -d db` (PostgreSQL 17 + pgvector on port 55432), or set DATABASE_URL=sqlite:///./regulens.db.',
          'Demo data: `python scripts/seed_demo.py --reset` from backend/.',
        ].map((s, i) => <Typography key={i} variant="body2" gutterBottom>• {s}</Typography>)}
        {data.counts && Object.keys(data.counts).length === 0 && <Empty message="No records yet." />}
      </Section>
    </Box>
  );
}
