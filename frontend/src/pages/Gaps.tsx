import React from 'react';
import {
  Box, Button, Drawer, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, fmt, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Gap { id: string; gap_uid: string; gap_type: string; title: string; status: string; severity_hint: string; truth_status: string; created_at: string; }
interface GapDetail extends Gap {
  description: string; detection_basis: string; detection_context: Record<string, unknown>;
  risk_assessments: Array<{ id: string; score: number; score_band: string; hard_constraint_flag?: string; explanation: { narrative: string; factors: Record<string, number>; weights: Record<string, number>; contributions: Record<string, number>; caveats: string[] }; calculated_at: string }>;
  remediation_actions: Array<{ id: string; action_uid: string; proposed_action: string; status: string; expected_risk_reduction: number }>;
  traceability: {
    obligation?: { uid: string; text: string; quoted_text: string; page?: number; section?: string; document_id?: string; review_status: string } | null;
    control?: { uid: string; title: string; design_effectiveness: string; operating_effectiveness: string } | null;
    regulatory_changes: Array<{ impact_uid: string; change_uid?: string; change_type?: string; summary?: string; impact_score?: number }>;
    risk?: { score: number; band: string } | null;
  };
}

export default function Gaps() {
  const [filters, setFilters] = React.useState({ status: 'open', gap_type: '', band: '', search: '', page: 0, page_size: 15 });
  const [data, setData] = React.useState<Page<Gap> | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [sel, setSel] = React.useState<GapDetail | null>(null);
  const [busy, setBusy] = React.useState('');
  const [accept, setAccept] = React.useState({ reason: '', approver: 'compliance.officer' });

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams({ page: String(filters.page + 1), page_size: String(filters.page_size) });
      if (filters.status) q.set('status', filters.status);
      if (filters.gap_type) q.set('gap_type', filters.gap_type);
      if (filters.band) q.set('band', filters.band);
      if (filters.search) q.set('search', filters.search);
      setData(await get<Page<Gap>>(`/api/v1/gaps?${q}`));
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [filters]);
  React.useEffect(() => { load(); }, [load]);

  const rescore = async () => {
    setBusy('risk'); setError(null);
    try { await post('/api/v1/risks/recalculate', {}); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const acceptRisk = async () => {
    if (!sel || accept.reason.length < 5 || !accept.approver) return;
    setBusy('accept'); setError(null);
    try {
      await post(`/api/v1/gaps/${sel.id}/accept-risk?reason=${encodeURIComponent(accept.reason)}&approver=${encodeURIComponent(accept.approver)}`, {});
      setSel(null); await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Compliance gap center</Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={1} sx={{ mb: 1 }}>
        <Grid size={{ xs: 6, md: 2 }}>
          <TextField select fullWidth size="small" label="Status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value, page: 0 })}>
            <MenuItem value="">all</MenuItem>
            {['open', 'in_remediation', 'closed', 'accepted_risk'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField fullWidth size="small" label="Gap type contains" value={filters.gap_type} onChange={(e) => setFilters({ ...filters, gap_type: e.target.value, page: 0 })} />
        </Grid>
        <Grid size={{ xs: 6, md: 2 }}>
          <TextField select fullWidth size="small" label="Risk band" value={filters.band} onChange={(e) => setFilters({ ...filters, band: e.target.value, page: 0 })}>
            <MenuItem value="">all</MenuItem>
            {['critical', 'high', 'medium', 'low'].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <TextField fullWidth size="small" label="Search title" value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value, page: 0 })} />
        </Grid>
        <Grid size={{ xs: 12, md: 2 }}>
          <Button fullWidth variant="outlined" disabled={busy === 'risk'} onClick={rescore}>{busy === 'risk' ? 'Scoring…' : 'Recalculate risk'}</Button>
        </Grid>
      </Grid>
      {loading ? <Loading what="gaps" /> : !data || data.items.length === 0 ? <Empty message="No gaps match. Genuine gaps appear here only when the engine detects them." /> : (
        <Section title={`${data.total} gap(s)`}>
          <Table size="small">
            <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Title</TableCell><TableCell>Type</TableCell><TableCell>Truth</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
            <TableBody>
              {data.items.map((g) => (
                <TableRow key={g.id} hover onClick={async () => setSel(await get<GapDetail>(`/api/v1/gaps/${g.id}`))} sx={{ cursor: 'pointer' }}>
                  <TableCell>{g.gap_uid}</TableCell><TableCell>{g.title}</TableCell><TableCell>{g.gap_type}</TableCell>
                  <TableCell><StatusChip value={g.truth_status} label={g.truth_status === 'determinate' ? 'demonstrated failure' : 'cannot determine'} /></TableCell>
                  <TableCell><StatusChip value={g.status} /></TableCell>
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
      <Drawer anchor="right" open={!!sel} onClose={() => setSel(null)} PaperProps={{ sx: { width: 640, p: 2 } }}>
        {sel && (
          <Box>
            <Typography variant="h6">{sel.gap_uid}</Typography>
            <Typography variant="body2" gutterBottom>{sel.title}</Typography>
            <Typography variant="body2" gutterBottom>{sel.description}</Typography>
            <Section title="Why this gap exists (detection basis)">
              <Typography variant="body2">{sel.detection_basis}</Typography>
              <Typography variant="body2">Truth status: <StatusChip value={sel.truth_status} /> (a genuine control failure vs an inability to determine compliance)</Typography>
            </Section>
            <Section title="Traceability chain">
              <Typography variant="body2"><strong>Obligation:</strong> {sel.traceability.obligation ? `${sel.traceability.obligation.uid} — ${sel.traceability.obligation.text.slice(0, 200)}` : 'none linked'}</Typography>
              {sel.traceability.obligation && (
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>“{(sel.traceability.obligation.quoted_text ?? '').slice(0, 300)}”</Typography>
              )}
              <Typography variant="body2"><strong>Control:</strong> {sel.traceability.control ? `${sel.traceability.control.uid} — ${sel.traceability.control.title}` : 'none linked'}</Typography>
              <Typography variant="body2"><strong>Regulatory changes:</strong> {sel.traceability.regulatory_changes.length === 0 ? 'none' : sel.traceability.regulatory_changes.map((c) => c.change_uid).join(', ')}</Typography>
            </Section>
            <Section title="Risk explanation">
              {sel.risk_assessments.length === 0 ? <Empty message="Not scored yet — use Recalculate risk." /> : (() => {
                const r = sel.risk_assessments[sel.risk_assessments.length - 1];
                return (
                  <Box>
                    <Typography variant="h6">Score {fmt(r.score)}/100 — <StatusChip value={r.score_band} /></Typography>
                    {r.hard_constraint_flag && <Typography variant="body2" color="error">Hard constraint: {r.hard_constraint_flag}</Typography>}
                    <Typography variant="body2" gutterBottom>{r.explanation.narrative}</Typography>
                    <Table size="small">
                      <TableHead><TableRow><TableCell>Factor</TableCell><TableCell>Value</TableCell><TableCell>Weight</TableCell><TableCell>Contribution</TableCell></TableRow></TableHead>
                      <TableBody>
                        {Object.keys(r.explanation.factors ?? {}).map((k) => (
                          <TableRow key={k}>
                            <TableCell>{k}</TableCell><TableCell>{fmt(r.explanation.factors[k])}</TableCell>
                            <TableCell>{fmt((r.explanation.weights ?? {})[k])}</TableCell>
                            <TableCell>{fmt((r.explanation.contributions ?? {})[k])}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    {(r.explanation.caveats ?? []).map((c, i) => <Typography key={i} variant="caption" display="block">• {c}</Typography>)}
                  </Box>
                );
              })()}
            </Section>
            <Section title={`Remediation (${sel.remediation_actions.length})`}>
              {sel.remediation_actions.map((a) => (
                <Typography key={a.id} variant="body2" gutterBottom>
                  <StatusChip value={a.status} /> {a.action_uid} — {a.proposed_action.slice(0, 160)}
                </Typography>
              ))}
            </Section>
            <Section title="Accept risk (requires approver + reason)">
              <Grid container spacing={1}>
                <Grid size={{ xs: 12 }}><TextField fullWidth size="small" label="Reason (min 5 chars)" value={accept.reason} onChange={(e) => setAccept({ ...accept, reason: e.target.value })} /></Grid>
                <Grid size={{ xs: 8 }}><TextField fullWidth size="small" label="Approver" value={accept.approver} onChange={(e) => setAccept({ ...accept, approver: e.target.value })} /></Grid>
                <Grid size={{ xs: 4 }}><Button fullWidth variant="outlined" color="warning" disabled={accept.reason.length < 5 || !accept.approver || !!busy} onClick={acceptRisk}>Accept risk</Button></Grid>
              </Grid>
            </Section>
          </Box>
        )}
      </Drawer>
    </Box>
  );
}

