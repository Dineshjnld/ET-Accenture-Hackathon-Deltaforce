import React from 'react';
import {
  Box, Button, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, fmt, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Action {
  id: string; action_uid: string; gap_id: string; proposed_action: string; reason: string;
  expected_risk_reduction: number; estimated_cost_inr?: number; cost_is_estimate: boolean;
  estimated_duration_days?: number; duration_is_estimate: boolean; suggested_owner_role?: string;
  deadline?: string; verification_procedure?: string; required_evidence_for_closure?: string;
  status: string; uncertainty_notes?: string; required_capability?: string;
}

export default function Remediation() {
  const [filters, setFilters] = React.useState({ status: '', overdue_only: false, page: 0, page_size: 15 });
  const [data, setData] = React.useState<Page<Action> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState('');
  const [approver, setApprover] = React.useState('compliance.officer');
  const [closure, setClosure] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams({ page: String(filters.page + 1), page_size: String(filters.page_size) });
      if (filters.status) q.set('status', filters.status);
      if (filters.overdue_only) q.set('overdue_only', 'true');
      setData(await get<Page<Action>>(`/api/v1/remediation?${q}`));
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [filters]);
  React.useEffect(() => { load(); }, [load]);

  const generate = async () => {
    setBusy('gen'); setError(null);
    try { await post('/api/v1/remediation/generate', {}); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const decide = async (id: string, decision: 'approved' | 'rejected') => {
    setBusy(id + decision); setError(null);
    try {
      await post(`/api/v1/remediation/${id}/approve`, { decision, approver });
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const complete = async (id: string) => {
    if (!closure.trim()) { setError('Closure evidence is required to complete a remediation action.'); return; }
    setBusy(id + 'done'); setError(null);
    try {
      await post(`/api/v1/remediation/${id}/complete`, { closure_evidence: closure, completed_by: approver });
      setClosure(''); await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Remediation workspace</Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Unknown costs and durations are left unknown or marked as estimates — never fabricated.
        Closing a gap requires approval and closure evidence.
      </Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={1} sx={{ mb: 1 }}>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField select fullWidth size="small" label="Status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value, page: 0 })}>
            <MenuItem value="">all</MenuItem>
            {['proposed', 'approved', 'rejected', 'in_progress', 'completed', 'closed', 'overdue'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <Button fullWidth variant={filters.overdue_only ? 'contained' : 'outlined'} color="error" onClick={() => setFilters({ ...filters, overdue_only: !filters.overdue_only, page: 0 })}>
            Overdue only
          </Button>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField fullWidth size="small" label="Approver" value={approver} onChange={(e) => setApprover(e.target.value)} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <Button fullWidth variant="contained" disabled={busy === 'gen'} onClick={generate}>{busy === 'gen' ? 'Generating…' : 'Generate recommendations'}</Button>
        </Grid>
      </Grid>
      {loading ? <Loading what="remediation actions" /> : !data || data.items.length === 0 ? <Empty message="No remediation actions. Generate recommendations for open gaps." /> : (
        <Section title={`${data.total} action(s)`}>
          <Table size="small">
            <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Action</TableCell><TableCell>Risk↓</TableCell><TableCell>Cost (est?)</TableCell><TableCell>Days (est?)</TableCell><TableCell>Owner</TableCell><TableCell>Deadline</TableCell><TableCell>Status</TableCell><TableCell>Decide</TableCell></TableRow></TableHead>
            <TableBody>
              {data.items.map((a) => (
                <TableRow key={a.id} hover>
                  <TableCell>{a.action_uid}</TableCell>
                  <TableCell>
                    {a.proposed_action.slice(0, 180)}
                    <Box component="div"><Typography variant="caption">Verify: {(a.verification_procedure ?? '').slice(0, 140)}</Typography></Box>
                    <Box component="div"><Typography variant="caption">Closure evidence needed: {(a.required_evidence_for_closure ?? '').slice(0, 140)}</Typography></Box>
                    {a.uncertainty_notes && <Box component="div"><Typography variant="caption" color="warning.main">Assumption: {a.uncertainty_notes}</Typography></Box>}
                  </TableCell>
                  <TableCell>{fmt(a.expected_risk_reduction)}</TableCell>
                  <TableCell>{a.estimated_cost_inr == null ? 'unknown' : `${fmt(a.estimated_cost_inr)}${a.cost_is_estimate ? ' (est)' : ''}`}</TableCell>
                  <TableCell>{a.estimated_duration_days == null ? 'unknown' : `${a.estimated_duration_days}${a.duration_is_estimate ? ' (est)' : ''}`}</TableCell>
                  <TableCell>{a.suggested_owner_role ?? '—'}</TableCell>
                  <TableCell>{a.deadline ?? '—'}</TableCell>
                  <TableCell><StatusChip value={a.status} /></TableCell>
                  <TableCell>
                    <Button size="small" disabled={!approver || !!busy} onClick={() => decide(a.id, 'approved')}>Approve</Button>
                    <Button size="small" color="error" disabled={!approver || !!busy} onClick={() => decide(a.id, 'rejected')}>Reject</Button>
                    <Button size="small" color="success" disabled={!approver || !!busy} onClick={() => complete(a.id)}>Complete</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Box display="flex" gap={1} mt={1}>
            <TextField fullWidth size="small" label="Closure evidence (required for Complete)" value={closure} onChange={(e) => setClosure(e.target.value)} />
          </Box>
          <TablePagination component="div" count={data.total} page={filters.page} rowsPerPage={filters.page_size}
            onPageChange={(_, p) => setFilters({ ...filters, page: p })}
            onRowsPerPageChange={(e) => setFilters({ ...filters, page_size: Number(e.target.value), page: 0 })}
            rowsPerPageOptions={[10, 15, 25]} />
        </Section>
      )}
    </Box>
  );
}

