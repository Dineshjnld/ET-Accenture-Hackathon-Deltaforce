import React from 'react';
import { Box, Card, CardContent, Grid, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { get } from '../api/client';
import { Empty, ErrorBox, Loading, Section, StatusChip, usePage } from '../components/ui';

interface Dashboard {
  counts: Record<string, number>;
  risk: { total_assessed: number; bands: Record<string, number>; max_score?: number; mean_score?: number };
  evidence: { total: number; sufficient: number; stale: number; missing: number; coverage: number };
  recent_activity: Array<{ action: string; actor: string; entity_type: string; entity_uid?: string; created_at?: string; detail?: string }>;
  open_gaps_by_type: Record<string, number>;
  pending_reviews: Record<string, number>;
  demo_ready: boolean;
}

function ProviderStrip() {
  const { data } = usePage(() => get<Record<string, unknown>>('/api/v1/system/providers').catch(() => ({} as Record<string, unknown>)));
  if (!data || typeof data !== 'object' || !('mode' in data)) return null;
  const d = data as unknown as {
    mode?: string;
    gemini?: { available?: boolean; model?: string };
    cerebras?: { available?: boolean; model?: string };
    cerebras_ledger?: { total_spend_usd?: number; budget_usd?: number; disabled?: boolean };
    requests_made?: number; failed_requests?: number;
  };
  return (
    <Section title="AI provider status">
      <Typography variant="body2" gutterBottom>
        Execution mode <StatusChip value={d.mode ?? 'deterministic'} /> ·
        Gemini <StatusChip value={d.gemini?.available ? 'yes' : 'no'} label={d.gemini?.available ? `available (${d.gemini?.model ?? '?'})` : 'unavailable'} /> ·
        Cerebras <StatusChip value={d.cerebras?.available ? 'yes' : 'no'} label={d.cerebras?.available ? `available (${d.cerebras?.model ?? '?'})` : (d.cerebras_ledger?.disabled ? 'disabled (budget guard)' : 'unavailable')} /> ·
        est. app spend ${Number(d.cerebras_ledger?.total_spend_usd ?? 0).toFixed(4)} of ${Number(d.cerebras_ledger?.budget_usd ?? 4).toFixed(2)} ·
        calls {d.requests_made ?? 0} ({d.failed_requests ?? 0} failed).
        {' '}Provider dashboard is authoritative for the real balance.
      </Typography>
    </Section>
  );
}
function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5">{value}</Typography>
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const { data, error, loading, reload } = usePage(() => get<Dashboard>('/api/v1/dashboard'));
  if (loading) return <Loading what="dashboard" />;
  if (error || !data) return <ErrorBox message={error ?? 'No data'} onRetry={reload} />;
  const c = data.counts;
  return (
    <Box>
      <Typography variant="h5" gutterBottom>Executive dashboard</Typography>
      <ProviderStrip />
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Every figure below is computed live from stored records. {data.demo_ready ? '' : 'Seed the demo dataset to populate this view.'}
      </Typography>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Regulations / documents" value={c.regulations} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Obligations (approved)" value={`${c.obligations} (${c.obligations_applicable})`} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Approved mappings" value={c.mapped_controls} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Open gaps" value={c.open_gaps} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="High-priority findings" value={c.high_priority_findings} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Evidence coverage" value={`${(data.evidence.coverage * 100).toFixed(1)}%`} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Overdue remediation" value={c.overdue_remediation} /></Grid>
        <Grid size={{ xs: 6, md: 3 }}><Stat label="Changes awaiting assessment" value={c.changes_awaiting_assessment} /></Grid>
      </Grid>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title="Risk bands (latest assessment per gap)">
            {Object.keys(data.risk.bands).length === 0 ? <Empty message="No risk assessments yet. Run Risks → Recalculate." /> : (
              <Table size="small">
                <TableHead><TableRow><TableCell>Band</TableCell><TableCell>Gaps</TableCell></TableRow></TableHead>
                <TableBody>
                  {Object.entries(data.risk.bands).map(([band, n]) => (
                    <TableRow key={band}><TableCell><StatusChip value={band} /></TableCell><TableCell>{n}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
          <Section title="Open gaps by type">
            {Object.keys(data.open_gaps_by_type).length === 0 ? <Empty message="No gaps recorded." /> : (
              <Table size="small">
                <TableHead><TableRow><TableCell>Gap type</TableCell><TableCell>Count</TableCell></TableRow></TableHead>
                <TableBody>
                  {Object.entries(data.open_gaps_by_type).map(([t, n]) => (
                    <TableRow key={t}><TableCell>{t}</TableCell><TableCell>{n}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
          <Section title="Pending human reviews">
            <Table size="small">
              <TableHead><TableRow><TableCell>Queue</TableCell><TableCell>Count</TableCell></TableRow></TableHead>
              <TableBody>
                {Object.entries(data.pending_reviews).map(([t, n]) => (
                  <TableRow key={t}><TableCell>{t}</TableCell><TableCell>{n}</TableCell></TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title="Evidence state">
            <Table size="small">
              <TableBody>
                <TableRow><TableCell>Total artefacts</TableCell><TableCell>{data.evidence.total}</TableCell></TableRow>
                <TableRow><TableCell>Sufficient</TableCell><TableCell>{data.evidence.sufficient}</TableCell></TableRow>
                <TableRow><TableCell>Stale</TableCell><TableCell>{data.evidence.stale}</TableCell></TableRow>
                <TableRow><TableCell>Missing / not reviewed</TableCell><TableCell>{data.evidence.missing}</TableCell></TableRow>
              </TableBody>
            </Table>
          </Section>
          <Section title="Recent workflow activity">
            {data.recent_activity.length === 0 ? <Empty message="No activity recorded yet." /> : (
              <Table size="small">
                <TableHead><TableRow><TableCell>Action</TableCell><TableCell>Actor</TableCell><TableCell>Target</TableCell><TableCell>Time</TableCell></TableRow></TableHead>
                <TableBody>
                  {data.recent_activity.slice(0, 12).map((a, i) => (
                    <TableRow key={i}>
                      <TableCell>{a.action}</TableCell>
                      <TableCell>{a.actor}</TableCell>
                      <TableCell>{a.entity_uid ?? a.entity_type}</TableCell>
                      <TableCell>{a.created_at?.replace('T', ' ').slice(0, 19) ?? '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
        </Grid>
      </Grid>
    </Box>
  );
}

