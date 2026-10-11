import React from 'react';
import {
  Box, Button, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface EvidenceItem {
  id: string; evidence_uid: string; title: string; validity_status: string;
  freshness_status: string; review_status: string; evidence_period_end?: string;
  test_result?: string; is_synthetic: boolean;
}

export default function Evidence() {
  const [filters, setFilters] = React.useState({ validity_status: '', page: 0, page_size: 15 });
  const [data, setData] = React.useState<Page<EvidenceItem> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [form, setForm] = React.useState({ title: '', control_ids: '', evidence_period_start: '', evidence_period_end: '', source: '' });
  const [reviewer, setReviewer] = React.useState('compliance.officer');
  const [busy, setBusy] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams({ page: String(filters.page + 1), page_size: String(filters.page_size) });
      if (filters.validity_status) q.set('validity_status', filters.validity_status);
      setData(await get<Page<EvidenceItem>>(`/api/v1/evidence?${q}`));
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [filters]);
  React.useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy('create'); setError(null);
    try {
      await post('/api/v1/evidence', {
        title: form.title,
        control_ids: form.control_ids.split(',').map((s) => s.trim()).filter(Boolean),
        evidence_period_start: form.evidence_period_start || null,
        evidence_period_end: form.evidence_period_end || null,
        source: form.source || null,
      });
      setForm({ title: '', control_ids: '', evidence_period_start: '', evidence_period_end: '', source: '' });
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const review = async (id: string, decision: 'approved' | 'rejected') => {
    setBusy(id + decision); setError(null);
    try {
      await post(`/api/v1/evidence/${id}/review?decision=${decision}&reviewer=${encodeURIComponent(reviewer)}`, {});
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Evidence workspace</Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Box display="flex" gap={1} mb={1}>
            <TextField select size="small" label="Validity" value={filters.validity_status} onChange={(e) => setFilters({ ...filters, validity_status: e.target.value, page: 0 })} sx={{ minWidth: 200 }}>
              <MenuItem value="">all</MenuItem>
              {['sufficient', 'insufficient', 'missing', 'stale', 'inconclusive', 'not_reviewed'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
            </TextField>
            <TextField size="small" label="Reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
          </Box>
          {loading ? <Loading what="evidence" /> : !data || data.items.length === 0 ? <Empty message="No evidence artefacts." /> : (
            <Section title={`${data.total} artefact(s)`}>
              <Table size="small">
                <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Title</TableCell><TableCell>Validity</TableCell><TableCell>Freshness</TableCell><TableCell>Review</TableCell><TableCell>Actions</TableCell></TableRow></TableHead>
                <TableBody>
                  {data.items.map((e) => (
                    <TableRow key={e.id} hover>
                      <TableCell>{e.evidence_uid}</TableCell><TableCell>{e.title}</TableCell>
                      <TableCell><StatusChip value={e.validity_status} /></TableCell>
                      <TableCell>{e.freshness_status}</TableCell>
                      <TableCell><StatusChip value={e.review_status} /></TableCell>
                      <TableCell>
                        <Button size="small" disabled={!reviewer || !!busy} onClick={() => review(e.id, 'approved')}>Approve</Button>
                        <Button size="small" color="error" disabled={!reviewer || !!busy} onClick={() => review(e.id, 'rejected')}>Reject</Button>
                      </TableCell>
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
          <Section title="Register evidence">
            <Grid container spacing={1}>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Title *" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Control IDs (comma-separated)" value={form.control_ids} onChange={(e) => setForm({ ...form, control_ids: e.target.value })} helperText="Find control IDs in the Control framework page." /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" type="date" label="Period start" InputLabelProps={{ shrink: true }} value={form.evidence_period_start} onChange={(e) => setForm({ ...form, evidence_period_start: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" type="date" label="Period end" InputLabelProps={{ shrink: true }} value={form.evidence_period_end} onChange={(e) => setForm({ ...form, evidence_period_end: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Source" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}><Button variant="contained" disabled={!form.title || busy === 'create'} onClick={create}>{busy === 'create' ? 'Saving…' : 'Register evidence'}</Button></Grid>
            </Grid>
          </Section>
        </Grid>
      </Grid>
    </Box>
  );
}

