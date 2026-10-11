import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './App';
import Dashboard from './pages/Dashboard';
import Regulations from './pages/Regulations';
import Obligations from './pages/Obligations';
import Controls from './pages/Controls';
import Evidence from './pages/Evidence';
import Gaps from './pages/Gaps';
import Changes from './pages/Changes';
import Remediation from './pages/Remediation';
import Scenarios from './pages/Scenarios';
import Agents from './pages/Agents';
import Settings from './pages/Settings';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<Dashboard />} />
          <Route path="regulations" element={<Regulations />} />
          <Route path="obligations" element={<Obligations />} />
          <Route path="controls" element={<Controls />} />
          <Route path="evidence" element={<Evidence />} />
          <Route path="gaps" element={<Gaps />} />
          <Route path="changes" element={<Changes />} />
          <Route path="remediation" element={<Remediation />} />
          <Route path="scenarios" element={<Scenarios />} />
          <Route path="agents" element={<Agents />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
