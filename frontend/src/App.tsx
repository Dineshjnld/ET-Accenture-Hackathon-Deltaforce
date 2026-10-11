import React from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import {
  AppBar, Box, CssBaseline, Divider, Drawer, List, ListItemButton, ListItemText,
  ThemeProvider, Toolbar, Typography, createTheme,
} from '@mui/material';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#0f2a44' },
    secondary: { main: '#0e7c6b' },
    background: { default: '#f4f6f9' },
  },
  typography: { fontFamily: '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif' },
});

const NAV: Array<[string, string]> = [
  ['/', 'Executive dashboard'],
  ['/regulations', 'Regulatory library'],
  ['/obligations', 'Obligation explorer'],
  ['/controls', 'Control framework'],
  ['/evidence', 'Evidence workspace'],
  ['/gaps', 'Compliance gap center'],
  ['/changes', 'Regulatory change center'],
  ['/remediation', 'Remediation workspace'],
  ['/scenarios', 'Scenario simulator'],
  ['/agents', 'Agents & audit center'],
  ['/settings', 'Settings & system health'],
];

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ display: 'flex' }}>
        <AppBar position="fixed" sx={{ zIndex: 1300 }}>
          <Toolbar>
            <Typography variant="h6" noWrap>
              ReguLens AI
            </Typography>
            <Typography variant="body2" sx={{ ml: 2, opacity: 0.8 }}>
              From changing regulations to evidence-backed compliance decisions.
            </Typography>
          </Toolbar>
        </AppBar>
        <Drawer variant="permanent" sx={{ width: 250, [`& .MuiDrawer-paper`]: { width: 250, boxSizing: 'border-box' } }}>
          <Toolbar />
          <Box sx={{ overflow: 'auto' }}>
            <List>
              {NAV.map(([to, label]) => (
                <ListItemButton
                  key={to}
                  component={NavLink}
                  to={to}
                  end={to === '/'}
                  sx={{ '&.active': { backgroundColor: 'action.selected', borderLeft: '4px solid #0e7c6b' } }}
                >
                  <ListItemText primary={label} />
                </ListItemButton>
              ))}
            </List>
            <Divider />
            <Box p={2}>
              <Typography variant="caption" color="text.secondary">
                Decision-support prototype. Not legal advice. All verdicts require human approval.
              </Typography>
            </Box>
          </Box>
        </Drawer>
        <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
          <Toolbar />
          <Outlet />
        </Box>
      </Box>
    </ThemeProvider>
  );
}
