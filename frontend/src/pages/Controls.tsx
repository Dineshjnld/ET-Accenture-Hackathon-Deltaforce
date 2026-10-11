import React from 'react';
import {
  Box, Button, Drawer, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Control {
  id: string; control_uid: string; title: string; control_type: string;
  design_effectiveness: string; operating_effectiveness: string; status: string;
  owner_role?: string; frequency?: string; is_synthetic: boolean;
}
interface ControlDetail extends Control {
  control_objective?: string; control_activity?: string; expected_evidence?: string;
  testing_procedure?: string; design_rationale?: string; operating_rationale?: string;
  evidence: Array<{ id: string; evidence_uid: string; title: string; validity_status: string; freshness_status: string; review_status: string }>;
  tests: Array<{ id: string; test_uid: string; test_type: string; result: string; result_rationale?: string; performed_at?: string }>;
  mappings: Array<{ id: string; obligation_id: string; status: string; confidence: number }>;
  evidence_summary: Record<string, number>;
}

const EMPTY_FORM = { title: '', control_objective: '', control_activity: '', owner_role: '', frequency: '', control_type: 'unknown', expected_evidence: '', testing_procedure: '' };

export default function Controls() {
  const [filters, setFilters] = React.useState({ search: '', control_type: '', page: 0, page_size: 15 });
  const [data, setData] = React.useState<Page<Control> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [sel, setSel] = React.useState<ControlDetail | null>(null);
  const [form, setForm] = React.useState(EMPTY_FORM);
  const [busy, setBusy] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams({ page: String(filters.page + 1), page_size: String(filters.page_size) });
      if (filters.search) q.set('search', filters.search);
      if (filters.control_type) q.set('control_type', filters.control_type);
      setData(await get<Page<Control>>(`/api/v1/controls?${q}`));
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [filters]);
  React.useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy('create'); setError(null);
    try {
      await post('/api/v1/controls', { ...form, is_synthetic: false });
      setForm(EMPTY_FORM); await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const runTests = async () => {
    setBusy('tests'); setError(null);
    try {
      await post('/api/v1/tests/run', {});
      if (sel) setSel(await get<ControlDetail>(`/api/v1/controls/${sel.id}`));
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Control framework</Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Grid container spacing={1} sx={{ mb: 1 }}>
            <Grid size={{ xs: 8 }}><TextField fullWidth size="small" label="Search controls" value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value, page: 0 })} /></Grid>
            <Grid size={{ xs: 4 }}>
              <TextField select fullWidth size="small" label="Type" value={filters.control_type} onChange={(e) => setFilters({ ...filters, control_type: e.target.value, page: 0 })}>
                <MenuItem value="">all</MenuItem>
                {['preventive', 'detective', 'corrective', 'unknown'].map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}
              </TextField>
            </Grid>
          </Grid>
          {loading ? <Loading what="controls" /> : !data || data.items.length === 0 ? <Empty message="No controls found." /> : (
            <Section title={`${data.total} control(s)`} action={<Button size="small" disabled={busy === 'tests'} onClick={runTests}>{busy === 'tests' ? 'Testing…' : 'Run all tests'}</Button>}>
              <Table size="small">
                <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Title</TableCell><TableCell>Design</TableCell><TableCell>Operating</TableCell></TableRow></TableHead>
                <TableBody>
                  {data.items.map((c) => (
                    <TableRow key={c.id} hover onClick={async () => setSel(await get<ControlDetail>(`/api/v1/controls/${c.id}`))} sx={{ cursor: 'pointer' }}>
                      <TableCell>{c.control_uid}</TableCell><TableCell>{c.title}</TableCell>
                      <TableCell><StatusChip value={c.design_effectiveness} /></TableCell>
                      <TableCell><StatusChip value={c.operating_effectiveness} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <TablePagination component="div" count={data.total} page={filters.page} rowsPerPage={filters.page_size}
                onPageChange={(_, p) => setFilters({ ...filters, page: p })}
                onRowsPerPageChange={(e) => setFilters({ ...filters, page_size: Number(e.target.value), page: 0 })}
                rowsPerPageOptions={[10, 15, 25]} />
            </Section>
          )}
        </Grid>
        <Grid size={{ xs: 12, md: 5 }}>
          <Section title="Create control">
            <Grid container spacing={1}>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Title *" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" multiline rows={2} label="Objective" value={form.control_objective} onChange={(e) => setForm({ ...form, control_objective: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" multiline rows={2} label="Activity" value={form.control_activity} onChange={(e) => setForm({ ...form, control_activity: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Owner role" value={form.owner_role} onChange={(e) => setForm({ ...form, owner_role: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Frequency" value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}>
                <TextField select fullWidth size="small" label="Type" value={form.control_type} onChange={(e) => setForm({ ...form, control_type: e.target.value })}>
                  {['preventive', 'detective', 'corrective', 'unknown'].map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}
                </TextField>
              </Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Expected evidence" value={form.expected_evidence} onChange={(e) => setForm({ ...form, expected_evidence: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" multiline rows={2} label="Testing procedure" value={form.testing_procedure} onChange={(e) => setForm({ ...form, testing_procedure: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><Button variant="contained" disabled={!form.title || busy === 'create'} onClick={create}>{busy === 'create' ? 'Creating…' : 'Create control'}</Button></Grid>
            </Grid>
          </Section>
        </Grid>
      </Grid>
      <Drawer anchor="right" open={!!sel} onClose={() => setSel(null)} PaperProps={{ sx: { width: 600, p: 2 } }}>
        {sel && (
          <Box>
            <Typography variant="h6">{sel.control_uid} — {sel.title}</Typography>
            <Typography variant="body2">Design: <StatusChip value={sel.design_effectiveness} /> Operating: <StatusChip value={sel.operating_effectiveness} /></Typography>
            <Section title="Definition">
              <Typography variant="body2">Objective: {sel.control_objective ?? '—'}</Typography>
              <Typography variant="body2">Activity: {sel.control_activity ?? '—'}</Typography>
              <Typography variant="body2">Design rationale: {sel.design_rationale ?? '—'}</Typography>
              <Typography variant="body2">Operating rationale: {sel.operating_rationale ?? '—'}</Typography>
              <Typography variant="body2">Evidence summary: {JSON.stringify(sel.evidence_summary)}</Typography>
            </Section>
            <Section title={`Evidence (${sel.evidence.length})`}>
              {sel.evidence.length === 0 ? <Empty message="No evidence linked." /> : sel.evidence.map((e) => (
                <Typography key={e.id} variant="body2" gutterBottom>
                  <StatusChip value={e.validity_status} /> {e.evidence_uid} — {e.title} ({e.freshness_status}, {e.review_status})
                </Typography>
              ))}
            </Section>
            <Section title={`Tests (${sel.tests.length})`}>
              {sel.tests.length === 0 ? <Empty message="Never tested." /> : sel.tests.map((t) => (
                <Typography key={t.id} variant="body2" gutterBottom>
                  <StatusChip value={t.result} /> {t.test_uid} [{t.test_type}] — {t.result_rationale ?? ''}
                </Typography>
              ))}
            </Section>
            <Section title={`Obligation mappings (${sel.mappings.length})`}>
              {sel.mappings.length === 0 ? <Empty message="No mappings." /> : sel.mappings.map((m) => (
                <Typography key={m.id} variant="body2" gutterBottom>
                  <StatusChip value={m.status} /> {m.obligation_id} · conf {m.confidence}
                </Typography>
              ))}
            </Section>
          </Box>
        )}
      </Drawer>
    </Box>
  );
}

