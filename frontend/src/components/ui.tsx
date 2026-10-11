import React from 'react';
import { Alert, Box, Chip, CircularProgress, Paper, Typography } from '@mui/material';

export function statusColor(s?: string): 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning' {
  const v = (s ?? '').toLowerCase();
  if (['approved', 'effective', 'effective_by_design', 'sufficient', 'pass', 'closed', 'succeeded', 'low'].includes(v)) return 'success';
  if (['high', 'critical', 'failed', 'fail', 'ineffective', 'overdue', 'breached'].includes(v)) return 'error';
  if (['medium', 'pending', 'candidate', 'proposed', 'running', 'partially_effective', 'partially_addressed', 'stale', 'insufficient', 'inconclusive', 'not_reviewed', 'in_progress', 'in_remediation'].includes(v)) return 'warning';
  if (['rejected', 'not_tested', 'missing', 'not_performed', 'failed'].includes(v)) return 'error';
  if (['open', 'draft', 'needs_revision', 'accepted_risk'].includes(v)) return 'info';
  return 'default';
}

export function StatusChip({ value, label }: { value?: string | null; label?: string }) {
  return <Chip size="small" color={statusColor(value ?? undefined)} label={label ?? value ?? '—'} variant="outlined" />;
}

export function Loading({ what }: { what: string }) {
  return (
    <Box display="flex" alignItems="center" gap={2} p={3} role="status" aria-label={`loading ${what}`}>
      <CircularProgress size={22} />
      <Typography variant="body2">Loading {what}…</Typography>
    </Box>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Box p={2}>
      <Alert severity="error">
        {message}
        {onRetry ? (
          <Box component="span" sx={{ ml: 2 }}>
            <Chip label="Retry" size="small" onClick={onRetry} color="error" />
          </Box>
        ) : null}
      </Alert>
    </Box>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', color: 'text.secondary' }}>
      <Typography variant="body2">{message}</Typography>
    </Paper>
  );
}

export function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
        {action}
      </Box>
      {children}
    </Paper>
  );
}

export function usePage<T>(loader: () => Promise<T>) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [nonce, setNonce] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    setLoading(true);
    loader()
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
