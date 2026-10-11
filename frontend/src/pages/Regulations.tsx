import React from 'react';
import {
  Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Grid, MenuItem,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import { Page, get, post, uploadDocument } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip, usePage } from '../components/ui';

interface Regulation { id: string; code: string; title: string; regulator: string; jurisdiction: string; status: string; created_at: string; }
interface RegVersion { id: string; version_label: string; published_at?: string; effective_from?: string; is_current: boolean; document_id?: string; notes?: string; }
interface Doc { id: string; filename: string; source_kind: string; source_url?: string; publisher?: string; document_type?: string; page_count?: number; text_length: number; ocr_used: boolean; created_at: string; }
interface Change { id: string; change_uid: string; change_type: string; clause_ref?: string; summary: string; confidence: number; requires_human_review: boolean; review_status: string; }

export default function Regulations() {
  const [nonce, setNonce] = React.useState(0);
  const regs = usePage(() => get<Page<Regulation>>('/api/v1/regulations?page_size=50'));
  const docs = usePage(() => get<Page<Doc>>(`/api/v1/documents?page_size=50&v=${nonce}`));
  const [selReg, setSelReg] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<(Regulation & { versions: RegVersion[] }) | null>(null);
  const [compare, setCompare] = React.useState<{ from: string; to: string } | null>(null);
  const [changes, setChanges] = React.useState<Change[] | null>(null);
  const [summary, setSummary] = React.useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = React.useState('');
  const [err, setErr] = React.useState<string | null>(null);
  const [upload, setUpload] = React.useState({ file: null as File | null, source_kind: 'internal', source_url: '', publisher: '', reference_number: '' });

  const loadReg = async (id: string) => {
    setSelReg(id);
    setDetail(await get<Regulation & { versions: RegVersion[] }>(`/api/v1/regulations/${id}`));
    setChanges(null);
  };

  const doCompare = async () => {
    if (!selReg || !compare?.from || !compare?.to) return;
    setBusy('compare'); setErr(null);
    try {
      const r = await post<{ changes: Change[]; summary: Record<string, unknown> }>(
        `/api/v1/regulations/${selReg}/compare-versions`,
        { from_version_id: compare.from, to_version_id: compare.to });
      setChanges(r.changes); setSummary(r.summary);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(''); }
  };

  const doUpload = async () => {
    if (!upload.file) return;
    setBusy('upload'); setErr(null);
    try {
      const f = new FormData();
      f.append('file', upload.file);
      f.append('source_kind', upload.source_kind);
      if (upload.source_url) f.append('source_url', upload.source_url);
      if (upload.publisher) f.append('publisher', upload.publisher);
      if (upload.reference_number) f.append('reference_number', upload.reference_number);
      await uploadDocument(f);
      setUpload({ file: null, source_kind: 'internal', source_url: '', publisher: '', reference_number: '' });
      setNonce((n) => n + 1);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(''); }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Regulatory library</Typography>
      {err && <ErrorBox message={err} />}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 5 }}>
          <Section title="Regulations">
            {regs.loading ? <Loading what="regulations" /> : regs.error || !regs.data ? <ErrorBox message={regs.error ?? ''} onRetry={regs.reload} /> :
              regs.data.items.length === 0 ? <Empty message="No regulations yet. Seed the demo dataset." /> : (
                <Table size="small">
                  <TableHead><TableRow><TableCell>Code</TableCell><TableCell>Title</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
                  <TableBody>
                    {regs.data.items.map((r) => (
                      <TableRow key={r.id} hover selected={selReg === r.id} onClick={() => loadReg(r.id)} sx={{ cursor: 'pointer' }}>
                        <TableCell>{r.code}</TableCell><TableCell>{r.title}</TableCell>
                        <TableCell><StatusChip value={r.status} /></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>)}
          </Section>
          <Section title="Upload document (PDF, DOCX, TXT, HTML, CSV, JSON)">
            <Grid container spacing={1}>
              <Grid size={{ xs: 12 }}>
                <Button variant="outlined" component="label" fullWidth>
                  {upload.file ? upload.file.name : 'Choose file…'}
                  <input type="file" hidden onChange={(e) => setUpload({ ...upload, file: e.target.files?.[0] ?? null })} />
                </Button>
              </Grid>
              <Grid size={{ xs: 6 }}>
                <TextField select fullWidth size="small" label="Source kind" value={upload.source_kind} onChange={(e) => setUpload({ ...upload, source_kind: e.target.value })}>
                  <MenuItem value="official">official</MenuItem>
                  <MenuItem value="internal">internal</MenuItem>
                  <MenuItem value="synthetic">synthetic</MenuItem>
                </TextField>
              </Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Publisher" value={upload.publisher} onChange={(e) => setUpload({ ...upload, publisher: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Source URL" value={upload.source_url} onChange={(e) => setUpload({ ...upload, source_url: e.target.value })} /></Grid>
              <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Reference no." value={upload.reference_number} onChange={(e) => setUpload({ ...upload, reference_number: e.target.value })} /></Grid>
              <Grid size={{ xs: 12 }}>
                <Button variant="contained" disabled={!upload.file || busy === 'upload'} onClick={doUpload}>
                  {busy === 'upload' ? 'Uploading…' : 'Upload & ingest'}
                </Button>
              </Grid>
            </Grid>
          </Section>
        </Grid>
        <Grid size={{ xs: 12, md: 7 }}>
          <Section title="Documents">
            {docs.loading ? <Loading what="documents" /> : docs.error || !docs.data ? <ErrorBox message={docs.error ?? ''} onRetry={docs.reload} /> :
              docs.data.items.length === 0 ? <Empty message="No documents ingested." /> : (
                <Table size="small">
                  <TableHead><TableRow><TableCell>File</TableCell><TableCell>Kind</TableCell><TableCell>Type</TableCell><TableCell>Pages</TableCell><TableCell>Chars</TableCell><TableCell>OCR</TableCell></TableRow></TableHead>
                  <TableBody>
                    {docs.data.items.map((d) => (
                      <TableRow key={d.id} hover>
                        <TableCell title={d.source_url ?? ''}>{d.filename}</TableCell>
                        <TableCell><StatusChip value={d.source_kind} /></TableCell>
                        <TableCell>{d.document_type ?? '—'}</TableCell>
                        <TableCell>{d.page_count ?? '—'}</TableCell>
                        <TableCell>{d.text_length}</TableCell>
                        <TableCell>{d.ocr_used ? 'yes' : 'no'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>)}
          </Section>
          {detail && (
            <Section title={`Versions — ${detail.code}`}>
              <Table size="small">
                <TableHead><TableRow><TableCell>Label</TableCell><TableCell>Published</TableCell><TableCell>Effective</TableCell><TableCell>Current</TableCell></TableRow></TableHead>
                <TableBody>
                  {detail.versions.map((v) => (
                    <TableRow key={v.id}><TableCell>{v.version_label}</TableCell><TableCell>{v.published_at ?? '—'}</TableCell><TableCell>{v.effective_from ?? '—'}</TableCell><TableCell>{v.is_current ? 'yes' : 'no'}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
              <Box mt={2} display="flex" gap={1} alignItems="center" flexWrap="wrap">
                <TextField select size="small" label="From" value={compare?.from ?? ''} onChange={(e) => setCompare({ from: e.target.value, to: compare?.to ?? '' })} sx={{ minWidth: 200 }}>
                  {detail.versions.map((v) => <MenuItem key={v.id} value={v.id}>{v.version_label}</MenuItem>)}
                </TextField>
                <TextField select size="small" label="To" value={compare?.to ?? ''} onChange={(e) => setCompare({ from: compare?.from ?? '', to: e.target.value })} sx={{ minWidth: 200 }}>
                  {detail.versions.map((v) => <MenuItem key={v.id} value={v.id}>{v.version_label}</MenuItem>)}
                </TextField>
                <Button variant="contained" disabled={busy === 'compare' || !compare?.from || !compare?.to} onClick={doCompare}>
                  {busy === 'compare' ? 'Comparing…' : 'Compare versions'}
                </Button>
              </Box>
              {summary && (
                <Box mt={1}><Typography variant="body2">Changes: {JSON.stringify(summary)}</Typography></Box>
              )}
              {changes && (
                <Table size="small" sx={{ mt: 1 }}>
                  <TableHead><TableRow><TableCell>UID</TableCell><TableCell>Type</TableCell><TableCell>Clause</TableCell><TableCell>Summary</TableCell><TableCell>Conf.</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
                  <TableBody>
                    {changes.map((ch) => (
                      <TableRow key={ch.id}>
                        <TableCell>{ch.change_uid}</TableCell>
                        <TableCell><StatusChip value={ch.change_type} /></TableCell>
                        <TableCell>{ch.clause_ref ?? '—'}</TableCell>
                        <TableCell>{ch.summary}</TableCell>
                        <TableCell>{ch.confidence.toFixed(2)}</TableCell>
                        <TableCell>{ch.requires_human_review ? 'required' : '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Section>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}

