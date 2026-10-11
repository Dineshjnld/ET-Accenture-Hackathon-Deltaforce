import React from 'react';
import {
  Box, Button, Drawer, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Obligation {
  id: string; obligation_uid: string; quoted_text: string; obligation_text: string;
  responsible_party?: string; action_required?: string; source_page?: number; source_section?: string;
  source_document_id?: string; severity: string; extraction_confidence?: number;
  extraction_method: string; extraction_model?: string; review_status: string; is_synthetic: boolean;
  applicability_conditions: string[]; deadline_text?: string;
}
interface Mapping { id: string; control_id: string; mapping_rationale: string; confidence: number; status: string; proposed_by: string; }
interface OblDetail extends Obligation { mappings: Mapping[]; applicability_assessments: Array<{ applicable: string; rationale: string; assessed_by: string; created_at?: string }>; review_notes?: string; }

export default function Obligations() {
  const [filters, setFilters] = React.useState({ review_status: '', severity: '', search: '', page: 0, page_size: 15 });
  const [data, setData] = React.useState<Page<Obligation> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [sel, setSel] = React.useState<OblDetail | null>(null);
  const [reviewer, setReviewer] = React.useState('compliance.officer');
  const [busy, setBusy] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams({ page: String(filters.page + 1), page_size: String(filters.page_size) });
      if (filters.review_status) q.set('review_status', filters.review_status);
      if (filters.severity) q.set('severity', filters.severity);
      if (filters.search) q.set('search', filters.search);
      setData(await get<Page<Obligation>>(`/api/v1/obligations?${q}`));
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [filters]);

  React.useEffect(() => { load(); }, [load]);

  const open = async (id: string) => setSel(await get<OblDetail>(`/api/v1/obligations/${id}`));

  const decide = async (decision: 'approved' | 'rejected' | 'needs_revision') => {
    if (!sel) return;
    setBusy(decision);
    try {
      await post(`/api/v1/obligations/${sel.id}/approve`, { decision, reviewer });
      setSel(null); await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Obligation explorer</Typography>
      <Grid container spacing={1} sx={{ mb: 2 }}>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField select fullWidth size="small" label="Review status" value={filters.review_status} onChange={(e) => setFilters({ ...filters, review_status: e.target.value, page: 0 })}>
            <MenuItem value="">all</MenuItem>
            {['pending', 'approved', 'rejected', 'needs_revision'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField select fullWidth size="small" label="Severity" value={filters.severity} onChange={(e) => setFilters({ ...filters, severity: e.target.value, page: 0 })}>
            <MenuItem value="">all</MenuItem>
            {['critical', 'high', 'medium', 'low', 'unknown'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <TextField fullWidth size="small" label="Search obligation text" value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value, page: 0 })} />
        </Grid>
      </Grid>
      {error && <ErrorBox message={error} />}
      {loading ? <Loading what="obligations" /> : !data || data.items.length === 0 ? <Empty message="No obligations match the filters." /> : (
        <Section title={`${data.total} obligation(s)`}>
          <Table size="small">
            <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Obligation</TableCell><TableCell>Severity</TableCell><TableCell>Method</TableCell><TableCell>Status</TableCell><TableCell>Sample</TableCell></TableRow></TableHead>
            <TableBody>
              {data.items.map((o) => (
                <TableRow key={o.id} hover onClick={() => open(o.id)} sx={{ cursor: 'pointer' }}>
                  <TableCell>{o.obligation_uid}</TableCell>
                  <TableCell>{o.obligation_text.slice(0, 140)}</TableCell>
                  <TableCell><StatusChip value={o.severity} /></TableCell>
                  <TableCell>{o.extraction_method}{o.is_synthetic ? ' · synthetic' : ''}</TableCell>
                  <TableCell><StatusChip value={o.review_status} /></TableCell>
                  <TableCell>{o.is_synthetic ? 'yes' : 'no'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <TablePagination component="div" count={data.total} page={filters.page} rowsPerPage={filters.page_size}
            onPageChange={(_, p) => setFilters({ ...filters, page: p })}
            onRowsPerPageChange={(e) => setFilters({ ...filters, page_size: Number(e.target.value), page: 0 })}
            rowsPerPageOptions={[10, 15, 25, 50]} />
        </Section>
      )}
      <Drawer anchor="right" open={!!sel} onClose={() => setSel(null)} PaperProps={{ sx: { width: 560, p: 2 } }}>
        {sel && (
          <Box>
            <Typography variant="h6">{sel.obligation_uid}</Typography>
            <Typography variant="body2" gutterBottom>{sel.obligation_text}</Typography>
            <Section title="Source citation (verbatim)">
              <Typography variant="body2" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                “{sel.quoted_text}”
              </Typography>
              <Typography variant="caption">
                document {sel.source_document_id ?? '—'} · page {sel.source_page ?? '—'} · section {sel.source_section ?? '—'}
              </Typography>
            </Section>
            <Section title="Classification">
              <Typography variant="body2">Party: {sel.responsible_party ?? 'unknown'} · Action: {sel.action_required ?? 'unknown'} · Deadline: {sel.deadline_text ?? 'none stated'}</Typography>
              <Typography variant="body2">Severity: <StatusChip value={sel.severity} /> · Confidence: {sel.extraction_confidence ?? '—'} · {sel.extraction_method}{sel.extraction_model ? ` (${sel.extraction_model})` : ''}</Typography>
              {sel.applicability_conditions.length > 0 && (
                <Typography variant="body2">Applicability notes: {sel.applicability_conditions.join(' | ')}</Typography>
              )}
            </Section>
            <Section title="Applicability assessments">
              {sel.applicability_assessments.length === 0 ? <Empty message="Not assessed yet." /> : sel.applicability_assessments.map((a, i) => (
                <Typography key={i} variant="body2" gutterBottom>
                  <StatusChip value={a.applicable} /> {a.rationale} <em>({a.assessed_by})</em>
                </Typography>
              ))}
            </Section>
            <Section title={`Linked controls (${sel.mappings.length})`}>
              {sel.mappings.length === 0 ? <Empty message="No mappings proposed yet." /> : sel.mappings.map((m) => (
                <Typography key={m.id} variant="body2" gutterBottom>
                  <StatusChip value={m.status} /> {m.control_id} · conf {m.confidence} · {m.mapping_rationale.slice(0, 160)}
                </Typography>
              ))}
            </Section>
            <Box display="flex" gap={1} alignItems="center">
              <TextField size="small" label="Reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
              <Button variant="contained" color="success" disabled={!reviewer || !!busy} onClick={() => decide('approved')}>Approve</Button>
              <Button variant="outlined" color="error" disabled={!reviewer || !!busy} onClick={() => decide('rejected')}>Reject</Button>
            </Box>
          </Box>
        )}
      </Drawer>
    </Box>
  );
}

