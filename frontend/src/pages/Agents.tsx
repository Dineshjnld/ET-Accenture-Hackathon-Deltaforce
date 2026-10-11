import React from 'react';
import {
  Box, Button, MenuItem, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface AgentEvent {
  id: string; agent_name: string; sequence: number; status: string; attempt: number;
  duration_ms?: number; message?: string; error?: string; model_used?: string;
  rule_used?: string; warnings: string[]; citations: Array<Record<string, unknown>>;
}
interface AgentRun { id: string; run_uid: string; workflow: string; status: string; triggered_by?: string; started_at: string; finished_at?: string; duration_ms?: number; events: AgentEvent[]; }
interface AuditEvent { id: string; event_uid: string; actor: string; action: string; entity_type: string; entity_uid?: string; created_at: string; detail?: string; }

export default function Agents() {
  const [tab, setTab] = React.useState<'runs' | 'audit' | 'catalog'>('runs');
  const [runs, setRuns] = React.useState<Page<AgentRun> | null>(null);
  const [sel, setSel] = React.useState<AgentRun | null>(null);
  const [audit, setAudit] = React.useState<Page<AuditEvent> | null>(null);
  const [catalog, setCatalog] = React.useState<Array<{ agents: Array<{ name: string; purpose: string; permitted_tools: string[]; timeout_seconds: number; max_retries: number }>; workflows: Array<{ name: string; description: string; steps: Array<{ name: string; agent: string }> }> }> | null>(null);
  const [wf, setWf] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [r, a, c] = await Promise.all([
        get<Page<AgentRun>>('/api/v1/agents/runs?page_size=20'),
        get<Page<AuditEvent>>('/api/v1/audit/events?page_size=30'),
        get<Array<{ agents: Array<{ name: string; purpose: string; permitted_tools: string[]; timeout_seconds: number; max_retries: number }>; workflows: Array<{ name: string; description: string; steps: Array<{ name: string; agent: string }> }> }>>('/api/v1/agents/catalog'),
      ]);
      setRuns(r); setAudit(a); setCatalog(c);
      if (!wf && c[0]?.workflows.length) setWf(c[0].workflows[0].name);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [wf]);
  React.useEffect(() => { load(); }, [load]);

  const runWorkflow = async () => {
    if (!wf) return;
    setBusy('wf'); setError(null);
    try {
      await post('/api/v1/agents/workflows/run', { workflow: wf, params: {} });
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  if (loading) return <Loading what="agent center" />;
  return (
    <Box>
      <Typography variant="h5" gutterBottom>Agent execution & audit center</Typography>
      {error && <ErrorBox message={error} />}
      <Box display="flex" gap={1} mb={2}>
        {(['runs', 'audit', 'catalog'] as const).map((t) => (
          <Button key={t} variant={tab === t ? 'contained' : 'outlined'} onClick={() => setTab(t)}>{t}</Button>
        ))}
        <Box flexGrow={1} />
        <TextField select size="small" label="Workflow" value={wf} onChange={(e) => setWf(e.target.value)} sx={{ minWidth: 240 }}>
          {(catalog?.[0]?.workflows ?? []).map((w) => <MenuItem key={w.name} value={w.name}>{w.name}</MenuItem>)}
        </TextField>
        <Button variant="contained" disabled={!wf || busy === 'wf'} onClick={runWorkflow}>{busy === 'wf' ? 'Running…' : 'Run workflow'}</Button>
      </Box>

      {tab === 'runs' && (
        <Box display="flex" gap={2}>
          <Box flex={1}>
            <Section title={`Workflow runs (${runs?.total ?? 0}) — bounded steps, persisted state`}>
              {!runs || runs.items.length === 0 ? <Empty message="No runs yet. Trigger a workflow above." /> : (
                <Table size="small">
                  <TableHead><TableRow><TableCell>Run</TableCell><TableCell>Workflow</TableCell><TableCell>Status</TableCell><TableCell>Steps</TableCell><TableCell>Started</TableCell></TableRow></TableHead>
                  <TableBody>
                    {runs.items.map((r) => (
                      <TableRow key={r.id} hover onClick={async () => setSel(await get<AgentRun>(`/api/v1/agents/runs/${r.id}`))} sx={{ cursor: 'pointer' }}>
                        <TableCell>{r.run_uid}</TableCell><TableCell>{r.workflow}</TableCell>
                        <TableCell><StatusChip value={r.status} /></TableCell>
                        <TableCell>{r.events.length}</TableCell>
                        <TableCell>{r.started_at?.replace('T', ' ').slice(0, 19)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Section>
          </Box>
          <Box flex={1}>
            <Section title={sel ? `Run ${sel.run_uid} — events` : 'Run detail'}>
              {!sel ? <Empty message="Select a run to inspect structured events, retries, errors and citations." /> : sel.events.map((e) => (
                <Box key={e.id} sx={{ mb: 1, p: 1, border: '1px solid #ddd' }}>
                  <Typography variant="body2">
                    <StatusChip value={e.status} /> <strong>#{e.sequence} {e.agent_name}</strong> — {e.message ?? e.error ?? ''}
                  </Typography>
                  <Typography variant="caption" display="block">
                    attempt {e.attempt} · {e.duration_ms ?? '—'} ms · rule {e.rule_used ?? '—'} · model {e.model_used ?? 'deterministic'}
                  </Typography>
                  {(e.warnings ?? []).map((w, i) => <Typography key={i} variant="caption" display="block" color="warning.main">⚠ {w}</Typography>)}
                  {e.error && <Typography variant="caption" display="block" color="error">{e.error}</Typography>}
                  {(e.citations ?? []).length > 0 && (
                    <Typography variant="caption" display="block">citations: {JSON.stringify(e.citations).slice(0, 300)}</Typography>
                  )}
                </Box>
              ))}
            </Section>
          </Box>
        </Box>
      )}

      {tab === 'audit' && (
        <Section title={`Append-only audit trail (${audit?.total ?? 0}) — actor, action, target, before/after`}>
          {!audit || audit.items.length === 0 ? <Empty message="No audit events." /> : (
            <Table size="small">
              <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Actor</TableCell><TableCell>Action</TableCell><TableCell>Entity</TableCell><TableCell>UID</TableCell></TableRow></TableHead>
              <TableBody>
                {audit.items.map((a) => (
                  <TableRow key={a.id} hover title={a.detail ?? ''}>
                    <TableCell>{a.created_at?.replace('T', ' ').slice(0, 19)}</TableCell>
                    <TableCell>{a.actor}</TableCell><TableCell>{a.action}</TableCell>
                    <TableCell>{a.entity_type}</TableCell><TableCell>{a.entity_uid ?? '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      )}

      {tab === 'catalog' && (
        <Box>
          <Section title="Logical agents (purpose, tools, timeouts, retries)">
            <Table size="small">
              <TableHead><TableRow><TableCell>Agent</TableCell><TableCell>Purpose</TableCell><TableCell>Tools</TableCell><TableCell>Timeout</TableCell><TableCell>Retries</TableCell></TableRow></TableHead>
              <TableBody>
                {(catalog?.[0]?.agents ?? []).map((a) => (
                  <TableRow key={a.name}>
                    <TableCell>{a.name}</TableCell><TableCell>{a.purpose}</TableCell>
                    <TableCell>{a.permitted_tools.join(', ')}</TableCell>
                    <TableCell>{a.timeout_seconds}s</TableCell><TableCell>{a.max_retries}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
          <Section title="Workflows (bounded, explicit state machine)">
            {(catalog?.[0]?.workflows ?? []).map((w) => (
              <Box key={w.name} sx={{ mb: 1 }}>
                <Typography variant="body2"><strong>{w.name}</strong> — {w.description}</Typography>
                <Typography variant="caption">{w.steps.map((s) => `${s.name}(${s.agent})`).join(' → ')}</Typography>
              </Box>
            ))}
          </Section>
        </Box>
      )}
    </Box>
  );
}

