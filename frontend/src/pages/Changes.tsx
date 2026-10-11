import React from 'react';
import {
  Box, Button, Grid, MenuItem, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip } from '../components/ui';

interface Regulation { id: string; code: string; title: string; }
interface RegVersion { id: string; version_label: string; }
interface Change { id: string; change_uid: string; change_type: string; clause_ref?: string; summary: string; added_text?: string; removed_text?: string; confidence: number; requires_human_review: boolean; }
interface Impact {
  id: string; impact_uid: string; impact_score: number; priority_delta: number; narrative: string;
  affected_obligation_ids: string[]; affected_control_ids: string[]; affected_gap_ids: string[];
  affected_action_ids: string[]; uncertainty_flags: string[]; propagation_path: Array<Record<string, unknown>>;
  status: string;
}

export default function Changes() {
  const [regs, setRegs] = React.useState<Regulation[]>([]);
  const [regId, setRegId] = React.useState('');
  const [versions, setVersions] = React.useState<RegVersion[]>([]);
  const [from, setFrom] = React.useState('');
  const [to, setTo] = React.useState('');
  const [changes, setChanges] = React.useState<Change[]>([]);
  const [impacts, setImpacts] = React.useState<Impact[]>([]);
  const [selImpact, setSelImpact] = React.useState<Impact | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    get<Page<Regulation>>('/api/v1/regulations?page_size=50')
      .then((p) => { setRegs(p.items); setLoading(false); })
      .catch((e: Error) => { setError(e.message); setLoading(false); });
  }, []);

  const pickReg = async (id: string) => {
    setRegId(id); setChanges([]); setImpacts([]); setSelImpact(null);
    const d = await get<{ versions: RegVersion[] }>(`/api/v1/regulations/${id}`);
    setVersions(d.versions);
  };

  const compare = async () => {
    if (!regId || !from || !to) return;
    setBusy('compare'); setError(null);
    try {
      const r = await post<{ changes: Change[] }>(`/api/v1/regulations/${regId}/compare-versions`, { from_version_id: from, to_version_id: to });
      setChanges(r.changes);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  const analyze = async (changeId: string) => {
    setBusy(changeId); setError(null);
    try {
      const imp = await post<Impact>('/api/v1/impact/analyze', { change_id: changeId });
      setImpacts((prev) => [imp, ...prev]);
      setSelImpact(imp);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  if (loading) return <Loading what="regulations" />;
  return (
    <Box>
      <Typography variant="h5" gutterBottom>Regulatory change center</Typography>
      {error && <ErrorBox message={error} />}
      <Grid container spacing={1} sx={{ mb: 2 }}>
        <Grid size={{ xs: 12, md: 5 }}>
          <TextField select fullWidth size="small" label="Regulation" value={regId} onChange={(e) => pickReg(e.target.value)}>
            {regs.map((r) => <MenuItem key={r.id} value={r.id}>{r.code} — {r.title.slice(0, 60)}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 2 }}>
          <TextField select fullWidth size="small" label="From" value={from} onChange={(e) => setFrom(e.target.value)}>
            {versions.map((v) => <MenuItem key={v.id} value={v.id}>{v.version_label}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 6, md: 2 }}>
          <TextField select fullWidth size="small" label="To" value={to} onChange={(e) => setTo(e.target.value)}>
            {versions.map((v) => <MenuItem key={v.id} value={v.id}>{v.version_label}</MenuItem>)}
          </TextField>
        </Grid>
        <Grid size={{ xs: 12, md: 3 }}>
          <Button fullWidth variant="contained" disabled={!from || !to || busy === 'compare'} onClick={compare}>
            {busy === 'compare' ? 'Comparing…' : 'Compare versions'}
          </Button>
        </Grid>
      </Grid>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Section title={`Candidate changes (${changes.length}) — signals for review, not legal determinations`}>
            {changes.length === 0 ? <Empty message="Run a comparison to see candidate changes side by side." /> : (
              <Table size="small">
                <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Type</TableCell><TableCell>Clause</TableCell><TableCell>Summary</TableCell><TableCell>Impact</TableCell></TableRow></TableHead>
                <TableBody>
                  {changes.map((ch) => (
                    <TableRow key={ch.id} hover>
                      <TableCell>{ch.change_uid}</TableCell>
                      <TableCell><StatusChip value={ch.change_type} /></TableCell>
                      <TableCell>{ch.clause_ref ?? '—'}</TableCell>
                      <TableCell>
                        {ch.summary}
                        {ch.removed_text && <Box component="div" sx={{ color: 'error.main' }}><em>− {(ch.removed_text ?? '').slice(0, 220)}</em></Box>}
                        {ch.added_text && <Box component="div" sx={{ color: 'success.main' }}><em>+ {(ch.added_text ?? '').slice(0, 220)}</em></Box>}
                        {ch.requires_human_review && <Box component="div"><StatusChip value="pending" label="human review required" /></Box>}
                      </TableCell>
                      <TableCell>
                        <Button size="small" disabled={busy === ch.id} onClick={() => analyze(ch.id)}>
                          {busy === ch.id ? '…' : 'Analyze impact'}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
        </Grid>
        <Grid size={{ xs: 12, md: 5 }}>
          <Section title={`Impact assessments (${impacts.length})`}>
            {impacts.length === 0 ? <Empty message="No impact assessment in this session. Analyze a change." /> : impacts.map((imp) => (
              <Box key={imp.id} sx={{ mb: 1, cursor: 'pointer', border: selImpact?.id === imp.id ? '1px solid #0e7c6b' : undefined, p: 1 }} onClick={() => setSelImpact(imp)}>
                <Typography variant="body2"><strong>{imp.impact_uid}</strong> — score {imp.impact_score} (Δ priority {imp.priority_delta})</Typography>
                <Typography variant="caption">{imp.affected_obligation_ids.length} obligations · {imp.affected_control_ids.length} controls · {imp.affected_gap_ids.length} gaps · {imp.uncertainty_flags.length} uncertain</Typography>
              </Box>
            ))}
          </Section>
          {selImpact && (
            <Section title={`Impact ${selImpact.impact_uid}`}>
              <Typography variant="body2" gutterBottom>{selImpact.narrative}</Typography>
              <Typography variant="body2">Obligations: {selImpact.affected_obligation_ids.join(', ') || 'none'}</Typography>
              <Typography variant="body2">Controls: {selImpact.affected_control_ids.join(', ') || 'none'}</Typography>
              <Typography variant="body2">Gaps: {selImpact.affected_gap_ids.join(', ') || 'none'}</Typography>
              <Typography variant="body2">Actions: {selImpact.affected_action_ids.join(', ') || 'none'}</Typography>
              <Typography variant="body2" sx={{ mt: 1 }}><strong>Uncertain relations (flagged, not asserted):</strong></Typography>
              {selImpact.uncertainty_flags.length === 0 ? <Typography variant="body2">none</Typography> :
                selImpact.uncertainty_flags.map((u, i) => <Typography key={i} variant="body2">• {u}</Typography>)}
              <Typography variant="body2" sx={{ mt: 1 }}><strong>Propagation path (audit):</strong></Typography>
              {selImpact.propagation_path.map((p, i) => <Typography key={i} variant="caption" display="block">{JSON.stringify(p)}</Typography>)}
            </Section>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}

