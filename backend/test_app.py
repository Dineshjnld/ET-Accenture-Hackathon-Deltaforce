import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

r = client.get('/api/v1/health')
print('GET /api/v1/health:', r.status_code, r.json())

r = client.get('/api/v1/system/status')
print('GET /api/v1/system/status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('  keys:', [k for k in data.keys() if not k.startswith('_')])
    # Show a few key counts
    for k in ['documents', 'obligations', 'obligations_approved', 'gaps', 'gaps_open', 'remediation_actions', 'regulatory_changes', 'impact_assessments', 'scenarios', 'audit_events', 'agent_runs']:
        v = data.get(k, 'N/A')
        print(f'  {k}: {v}')