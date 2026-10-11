import React from 'react';
import {
  Box, Button, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import { Page, fmt, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Scenario { id: string; scenario_uid: string; name: string; strategy: string; description?: string; budget_inr?: number; capacity_days?: number; horizon_days?: number; }
interface SimResult {
  id: string; selected_actions: Array<{ action_uid: string; gap_uid: string; objective_contribution: number; expected_risk_reduction: number; gap_risk_score: number; estimated_cost_inr: number; cost_is_estimate: boolean; estimated_duration_days: number; duration_is_estimate: boolean; deadline?: string }>;
  rejected_actions: Array<{ action_uid: string; gap_uid: string; reason: string; forgone_value: number }>;
  constraint_checks: Array<{ constraint: string; limit: unknown; used: unknown; satisfied: boolean; note?: string }>;
  objective_value: number; projected_risk_reduction: number; projected_cost_inr: number;
  projected_duration_days: number; unresolved_gap_ids: string[]; solver: string;
  optimality_claim: string; explanation: { rows: Array<{ action_uid: string; selected: boolean; why: string }>; assumptions: string[]; deadline_violations: Array<Record<string, unknown>> };
  assumptions: string[]; is_prediction: boolean;
}

export default function Scenarios() {
  const [scenarios, setScenarios] = React.useState<Scenario[]>([]);
  const [sel, setSel] = React.useState<string>('');
  const [result, setResult] = React.useState<SimResult | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const [form, setForm] = React.useState({ name: '', strategy: 'max_risk_reduction', description: '', budget_inr: '', capacity_days: '', horizon_days: '' });

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const p = await get<Page<Scenario>>('/api/v1/scenarios?page_size=50');
      setScenarios(p.items);
      if (p.items.length > 0 && !sel) setSel(p.items[0].id);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [sel]);
  React.useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy('create'); setError(null);
    try {
      const s = await post<Scenario>('/api/v1/scenarios', {
        name: form.name, strategy: form.strategy, description: form.description || null,
        budget_inr: form.budget_inr ? Number(form.budget_inr) : null,
        capacity_days: form.capacity_days ? Number(form.capacity_days) : null,
        horizon_days: form.horizon_days ? Number(form.horizon_days) : null,
      });
      setForm({ name: '', strategy: 'max_risk_reduction', description: '', budget_inr: '', capacity_days: '', horizon_days: '' });
      await load(); setSel(s.id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const simulate = async () => {
    if (!sel) return;
    setBusy('sim'); setError(null);
    try {
      const r = await post<SimResult>(`/api/v1/scenarios/${sel}/simulate`, {});
      setResult(r);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  if (loading) return <Loading what="scenarios" />;
  return (
    <Box>
      <Typography variant="h5" gutterBottom>Scenario simulator</Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Compare remediation portfolios under budget, capacity and deadline constraints.
        All outputs are <strong>predictions</strong> with stated assumptions — not observed outcomes.
      </Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 4 }}>
          <Section title="Scenarios">
            {scenarios.length === 0 ? <Empty message="No scenarios yet — create one." /> : scenarios.map((s) => (
              <Box key={s.id} sx={{ mb: 1, cursor: 'pointer', border: sel === s.id ? '1px solid #0e7c6b' : undefined, p: 1 }} onClick={() => { setSel(s.id); setResult(null); }}>
                <Typography variant="body2"><strong>{s.scenario_uid}</strong> — {s.name}</Typography>
                <Typography variant="caption">{s.strategy} · budget {s.budget_inr ?? '∞'} · capacity {s.capacity_days ?? '∞'}d · horizon {s.horizon_days ?? '∞'}d</Typography>
              </Box>
            ))}
            <Button variant="contained" disabled={!sel || busy === 'sim'} onClick={simulate} sx={{ mt: 1 }}>
              {busy === 'sim' ? 'Solving…' : 'Simulate selected'}
            </Button>
          </Section>
          <Section title="New scenario">
            <Grid container spacing={1}>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Name *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}>
                <TextField select fullWidth size="small" label="Strategy" value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}>
                  <MenuItem value="max_risk_reduction">A · maximum risk reduction within budget</MenuItem>
                  <MenuItem value="shared_control">B · shared-control efficiency</MenuItem>
                  <MenuItem value="deadline_first">C · deadline-first</MenuItem>
                  <MenuItem value="custom">D · custom priority</MenuItem>
                </TextField>
              </Grid>
              <Grid size={{ xs: 4 }}><TextField fullWidth size="small" label="Budget INR" value={form.budget_inr} onChange={(e) => setForm({ ...form, budget_inr: e.target.value })} /></Grid>
              <Grid size={{ xs: 4 }}><TextField fullWidth size="small" label="Capacity days" value={form.capacity_days} onChange={(e) => setForm({ ...form, capacity_days: e.target.value })} /></Grid>
              <Grid size={{ xs: 4 }}><TextField fullWidth size="small" label="Horizon days" value={form.horizon_days} onChange={(e) => setForm({ ...form, horizon_days: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><Button variant="outlined" disabled={form.name.length < 3 || busy === 'create'} onClick={create}>Create scenario</Button></Grid>
            </Grid>
          </Section>
        </Grid>
        <Grid size={{ xs: 12, md: 8 }}>
          {!result ? <Empty message="Select a scenario and press Simulate to see projected risk reduction, cost, duration and unresolved gaps." /> : (
            <Box>
              <Section title={`Result — solver ${result.solver} · prediction`}>
                <Typography variant="body2">Objective: <strong>{fmt(result.objective_value)}</strong> · Projected risk reduction: {fmt(result.projected_risk_reduction)} · Cost: {fmt(result.projected_cost_inr)} · Duration: {result.projected_duration_days}d · Unresolved gaps: {result.unresolved_gap_ids.length}</Typography>
                <Typography variant="body2" sx={{ mt: 1 }}><em>{result.optimality_claim}</em></Typography>
              </Section>
              <Section title={`Selected actions (${result.selected_actions.length})`}>
                <Table size="small">
                  <TableHead><TableRow><TableCell>Action</TableCell><TableCell>Gap</TableCell><TableCell>Contribution</TableCell><TableCell>Cost</TableCell><TableCell>Days</TableCell><TableCell>Deadline</TableCell></TableRow></TableHead>
                  <TableBody>
                    {result.selected_actions.map((a) => (
                      <TableRow key={a.action_uid}>
                        <TableCell>{a.action_uid}</TableCell><TableCell>{a.gap_uid}</TableCell>
                        <TableCell>{fmt(a.objective_contribution)}</TableCell>
                        <TableCell>{fmt(a.estimated_cost_inr)}{a.cost_is_estimate ? ' (est)' : ''}</TableCell>
                        <TableCell>{a.estimated_duration_days}{a.duration_is_estimate ? ' (est)' : ''}</TableCell>
                        <TableCell>{a.deadline ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Section>
              <Section title={`Rejected alternatives (${result.rejected_actions.length})`}>
                {result.rejected_actions.map((r) => (
                  <Typography key={r.action_uid} variant="body2" gutterBottom>
                    {r.action_uid} ({r.gap_uid}): {r.reason} — forgone value {fmt(r.forgone_value)}
                  </Typography>
                ))}
              </Section>
              <Section title="Constraint checks">
                {result.constraint_checks.map((c, i) => (
                  <Typography key={i} variant="body2" gutterBottom>
                    <StatusChip value={c.satisfied ? 'pass' : 'fail'} label={c.satisfied ? 'satisfied' : 'VIOLATED'} /> {c.constraint}: used {JSON.stringify(c.used)} / limit {JSON.stringify(c.limit)}{c.note ? ` — ${c.note}` : ''}
                  </Typography>
                ))}
              </Section>
              <Section title="Why each action was selected">
                {(result.explanation.rows ?? []).map((r) => (
                  <Typography key={r.action_uid} variant="body2" gutterBottom>
                    <StatusChip value={r.selected ? 'pass' : 'default'} label={r.selected ? 'selected' : 'not selected'} /> {r.action_uid}: {r.why}
                  </Typography>
                ))}
              </Section>
              <Section title="Assumptions & uncertainty">
                {(result.assumptions ?? []).map((a, i) => <Typography key={i} variant="body2" gutterBottom>• {a}</Typography>)}
                {(result.explanation.deadline_violations ?? []).length > 0 && (
                  <Typography variant="body2" color="error">Deadline violations: {JSON.stringify(result.explanation.deadline_violations)}</Typography>
                )}
              </Section>
            </Box>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}

