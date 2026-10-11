# ReguLens AI — Agentic Regulatory Compliance Intelligence

> **From changing regulations to evidence-backed compliance decisions.**
> Decision-support prototype. Not legal advice. Every verdict requires human approval.

ReguLens AI ingests regulatory documents (RBI Master Directions, internal policies, control
matrices), extracts atomic obligations with verbatim citations, maps them to internal controls,
assesses evidence, detects gaps, scores risk transparently, plans remediation, and simulates
what-if portfolios — all through 13 bounded, audited logical agents.

**Execution philosophy:** deterministic Python rules are the ground truth and work with **zero
keys and zero downloads**. Hosted AI (Google **Gemini** + **Cerebras**) is strictly opt-in for
language tasks (obligation extraction, mapping candidates, remediation drafts), guarded by budgets,
caching, and honest fallback. Deterministic output is **never** presented as AI-generated.

---

## Table of contents

1. [Architecture](#1-architecture)
2. [AI providers: Gemini + Cerebras + deterministic](#2-ai-providers-gemini--cerebras--deterministic)
3. [Features & the 13 agents](#3-features--the-13-agents)
4. [Tech stack](#4-tech-stack)
5. [Repository layout](#5-repository-layout)
6. [Prerequisites](#6-prerequisites)
7. [Quickstart (5 minutes, no keys)](#7-quickstart-5-minutes-no-keys)
8. [Configuration (.env reference)](#8-configuration-env-reference)
9. [Running with Docker](#9-running-with-docker)
10. [Demo walkthrough](#10-demo-walkthrough)
11. [API reference](#11-api-reference)
12. [Observability & provider dashboard](#12-observability--provider-dashboard)
13. [Budget & privacy guards](#13-budget--privacy-guards)
14. [Testing](#14-testing)
15. [Evaluation dataset](#15-evaluation-dataset)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Architecture

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│   Frontend  │────▶│  Backend (FastAPI)   │────▶│  PostgreSQL 17 + pgvector│
│ Vite+React  │◀────│  /api/v1/*           │◀────│  (or SQLite for zero-dep) │
│ +MUI, 11pg  │     └──────────┬───────────┘     └─────────────────────────┘
└─────────────┘                │
                    ┌──────────▼───────────┐
                    │ 13 agents (native     │
                    │ state machine) +      │
                    │ deterministic services│
                    └──────────┬───────────┘
                               │  opt-in, bounded, cached
                    ┌──────────▼───────────┐
                    │ Gemini API │ Cerebras │  (keys server-side only)
                    │  API       │  API     │
                    └──────────────────────┘
```

- **Deterministic core** — parsers (PDF/DOCX/TXT/HTML/CSV/JSON), rule-based obligation
  extractor, BM25-lite + lexical-TFIDF hybrid retrieval, evidence state machine, weighted risk
  engine, template remediation engine, OR-Tools CP-SAT optimiser (with greedy fallback),
  contradiction/cross-regulation detectors. Every claim carries citations/audit trail.
- **Hosted AI layer** (`backend/app/llm/`) — provider-independent `LLMProvider` interface with
  `GeminiProvider`, `CerebrasProvider`, `DeterministicProvider`, one central `LLMRouter`
  (routing + sequential fallback + TTL cache + concurrency cap + usage ledger). Application
  modules call `get_router().generate_for_task_sync(task, ...)` — never a provider directly.
- **No local-LLM backend.** No model downloads, no local weights, no Ollama, no LAYA dependency.

## 2. AI providers: Gemini + Cerebras + deterministic

| Task | Implementation |
|---|---|
| PDF/DOCX text extraction | Deterministic parsers |
| Regulatory obligation extraction | **Gemini** (structured JSON, Pydantic-validated) |
| Regulatory version comparison | Deterministic diff + Gemini interpretation |
| Control mapping candidates | Hybrid retrieval + **Cerebras** (opt-in AI re-rank) |
| Evidence status | Deterministic tests (+ optional AI interpretation) |
| Risk scoring | Python rules engine (never an LLM) |
| Remediation proposals | Deterministic templates; **Cerebras** drafts opt-in (Gemini fallback) |
| Deadline enforcement | Deterministic rules |
| Portfolio optimisation | OR-Tools CP-SAT / deterministic greedy |
| Citation validation | Deterministic source-reference checks |
| Contradiction analysis | Retrieval + AI analysis + human review |
| Executive explanation | Gemini or Cerebras per budget/availability |

Fallback sequence per call: preferred provider → strict retries → alternative provider (if
enabled, available, within budget) → deterministic output where possible → explicit
`unavailable` / `review-required` status otherwise. Every API response and the UI expose the
actual `execution_mode` (`gemini` | `cerebras` | `deterministic` | `deterministic_fallback` | `cache`).

Supported modes (via env): **Gemini only · Cerebras only · both · neither (deterministic)** —
plus `DETERMINISTIC_MODE=true` to force local-only operation even with keys configured.

## 3. Features & the 13 agents

| # | Agent | What it does |
|---|---|---|
| 1 | `ingestion_agent` | Parse/store documents with provenance, injection screening |
| 2 | `regulatory_intelligence_agent` | Register versions, detect candidate changes |
| 3 | `obligation_extraction_agent` | Atomic obligations + verbatim quotes (deterministic; Gemini opt-in via `use_llm`) |
| 4 | `applicability_agent` | Explicit applicability rules per organisation profile |
| 5 | `control_mapping_agent` | Hybrid-retrieval candidates (Cerebras re-rank opt-in); human approval required |
| 6 | `evidence_assessment_agent` | Sufficiency / freshness / review-state assessment |
| 7 | `control_testing_agent` | Design vs operating effectiveness tests |
| 8 | `gap_analysis_agent` | Gap detection across obligations→controls→evidence→tests |
| 9 | `risk_assessment_agent` | Transparent weighted scoring (configurable weights, deadline breach → critical) |
| 10 | `remediation_planning_agent` | Templated recommendations (costs left unknown, never invented) |
| 11 | `regulatory_impact_agent` | Change propagation through the compliance graph |
| 12 | `scenario_analysis_agent` | Budget/capacity/deadline-constrained portfolio optimisation |
| 13 | `validation_audit_agent` | Contradiction detection, cross-regulation report, audit integrity |

Fixed DAGs (`ingest_and_extract`, `compliance_assessment`, `change_impact`,
`scenario_planning`, `evidence_review`): finite steps, bounded retries, correlation IDs,
persisted `agent_runs` / `agent_events`.

## 4. Tech stack

- **Backend:** Python ≥3.11, FastAPI, Uvicorn, Pydantic v2 (+ pydantic-settings), SQLAlchemy 2,
  Alembic, `google-genai`, `cerebras-cloud-sdk`, scikit-learn (lexical TF-IDF), OR-Tools,
  pdfplumber/pypdf/python-docx/bs4, structlog, httpx, PyJWT.
- **Database:** PostgreSQL 17 + pgvector (Docker) **or** SQLite (`DATABASE_URL=sqlite:///./regulens.db`).
- **Frontend:** Vite 6, React 19, TypeScript, MUI 7, react-router, Vitest + Testing Library.
- **Infra:** Docker Compose (db / backend / frontend), no GPU, no model downloads.

## 5. Repository layout

```
ET_Hack/
├── README.md                  ← you are here
├── .env.example               ← copy to .env (never commit .env or keys)
├── .gitignore
├── docker-compose.yml
├── backend/
│   ├── pyproject.toml         ← deps incl. google-genai, cerebras-cloud-sdk
│   ├── app/
│   │   ├── main.py            ← FastAPI factory (create_app)
│   │   ├── config.py          ← all settings incl. Gemini/Cerebras/budgets
│   │   ├── api/v1/            ← documents, compliance, system routers
│   │   ├── agents/            ← base contracts, 13 agents, orchestrator, workflows
│   │   ├── services/          ← deterministic engines + ai_assist.py (only hosted-AI touchpoint)
│   │   ├── llm/               ← base/errors/schemas/redaction/cache/ledger/
│   │   │                        gemini_provider/cerebras_provider/deterministic/router/
│   │   │                        embeddings (lexical TF-IDF only)/prompts
│   │   ├── models/ parsing/ retrieval/ db.py schemas.py security.py
│   ├── tests/                 ← pytest suite (incl. test_llm_providers.py, mocked)
│   ├── scripts/seed_demo.py   ← demo dataset seeder
│   └── eval/                  ← evaluation dataset + runner
├── frontend/src/              ← pages (11), components, api client
└── data/samples/              ← committed demo data (official RBI + synthetic)
    └── uploads/               ← LOCAL ONLY (git-ignored): uploads + derived text
```

## 6. Prerequisites

- Python 3.11+ (`python --version`), Node 18+ / npm, Docker Desktop (only for Postgres path).
- No API keys needed for the default deterministic path.

## 7. Quickstart (5 minutes, no keys)

**Backend (SQLite, zero dependencies):**
```powershell
cd backend
pip install -e ".[dev]"
$env:DATABASE_URL = "sqlite:///./regulens.db"
python -m uvicorn app.main:app --port 8000
# health:  http://127.0.0.1:8000/api/v1/health
# status:  http://127.0.0.1:8000/api/v1/system/status   (mode: deterministic)
```

**Seed the demo dataset** (synthetic + real RBI directions, clearly labelled):
```powershell
cd backend
python scripts/seed_demo.py --reset
```

**Frontend:**
```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173  (proxies /api/v1 → backend)
```

**Enable hosted AI (optional):**
```powershell
Copy-Item ..\.env.example ..\.env   # from ET_Hack root, then edit:
# GEMINI_API_KEY=<your-key>   (+ optional GEMINI_MODEL, default gemini-2.0-flash)
# CEREBRAS_API_KEY=<your-key> (+ optional CEREBRAS_MODEL, default gpt-oss-120b)
# LLM_PROVIDER=gemini  LLM_FALLBACK_PROVIDER=cerebras  DETERMINISTIC_MODE=false
```
Restart the backend. Settings → “AI providers” shows verification, spend, cache and fallbacks.

## 8. Configuration (.env reference)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | pgvector on `:55432` | `sqlite:///./regulens.db` for zero-dep runs |
| `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_ENABLED` | empty / `gemini-2.0-flash` / `true` | Gemini Developer API (model configurable; verified via account list) |
| `CEREBRAS_API_KEY` / `CEREBRAS_MODEL` / `CEREBRAS_ENABLED` | empty / `gpt-oss-120b` / `true` | Cerebras Cloud (verify model IDs against current docs) |
| `LLM_PROVIDER` / `LLM_FALLBACK_PROVIDER` | `gemini` / `cerebras` | Central routing preference |
| `LLM_ROUTING_OVERRIDES` | empty | JSON per-task overrides, e.g. `{"control_mapping":"cerebras"}` |
| `CEREBRAS_BUDGET_USD` | `4.00` | Application-level total guard (**not** an account cap) |
| `CEREBRAS_DAILY_BUDGET_USD` | `1.00` | Application-level daily guard |
| `CEREBRAS_MAX_CALL_COST_USD` | `0.05` | Per-call estimate ceiling; above needs explicit approval |
| `CEREBRAS_MAX_OUTPUT_TOKENS` / `CEREBRAS_MAX_RETRIES` | `1024` / `2` | Bounded outputs, strict retries |
| `GEMINI_MAX_OUTPUT_TOKENS` / `GEMINI_MAX_RETRIES` / `GEMINI_TIMEOUT_SECONDS` | `2048` / `2` / `60` | Bounded Gemini calls |
| `LLM_MAX_INPUT_CHARS` / `LLM_CACHE_ENABLED` / `LLM_MAX_CONCURRENT` | `24000` / `true` / `2` | Input cap, repeat-request cache, no unbounded parallelism |
| `DETERMINISTIC_MODE` | `false` | `true` forces local-only even with keys set |
| `AUTH_ENABLED` / `JWT_SECRET` | `false` / dev default | Enable + change secret for shared deployments |

Config validator supports all four modes (Gemini-only, Cerebras-only, both, neither) and
rejects negative budgets / empty model IDs. `.env` and real keys are git-ignored and redacted
from logs, errors and status payloads.

## 9. Running with Docker

```powershell
# Postgres + backend + frontend (deterministic; hosted AI disabled in compose by default)
docker compose up --build
# backend → http://localhost:8000/api/v1/health | frontend → http://localhost:8080
```
To enable hosted AI in Docker, set keys in `.env` and override `GEMINI_ENABLED`/`CEREBRAS_ENABLED`
for the backend service (keys stay server-side; never reach the browser).

## 10. Demo walkthrough

1. **Executive dashboard** (`/`) — live counts, risk bands, evidence coverage, pending reviews,
   plus the AI provider strip (mode, availability, estimated app spend).
2. **Regulatory library** — upload a PDF/DOCX/TXT (validated, stored server-side) or ingest a
   bundled RBI sample; compare two regulation versions (deterministic diff).
3. **Obligation explorer** — extract obligations (deterministic; toggle AI-assist to route one
   bounded Gemini call, Pydantic-validated, citations checked); approve/reject each (human gate).
4. **Control framework** — define controls; **suggest mappings** (hybrid retrieval; opt-in
   `use_ai` Cerebras re-rank, unknown control IDs rejected); approve each mapping.
5. **Evidence workspace** — link artefacts, run design/operating tests (deterministic state machines).
6. **Gap center → Remediation** — detect gaps, recalculate risk (deadline breach forces critical),
   generate templated actions (opt-in `use_ai` Cerebras drafts attached as labelled notes;
   template cost/duration never AI-invented); approve → complete with closure evidence.
7. **Scenario simulator** — optimise the portfolio under budget/capacity/deadlines
   (OR-Tools CP-SAT or greedy fallback; optimality claim always reported).
8. **Agents & audit** — run bounded workflows, inspect runs/events, contradiction &
   cross-regulation reports.
9. **Settings & system health** — provider availability + model IDs, execution mode, request/token
   counts, estimated Cerebras spend vs limits, cache hit rate, fallback events, privacy notes.

## 11. API reference

Base: `/api/v1`. All errors use `{error: {code, message, details}}`.

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /system/status` | Mode, counts, providers, ledger, cache, fallbacks, warnings |
| `GET /system/providers` | Focused hosted-AI observability (no secrets) |
| `GET /dashboard` | Executive aggregates |
| `POST /documents` (multipart) | Validated upload + ingest |
| `POST /documents/ingest-path` | Ingest a bundled sample file |
| `POST /regulations`, `POST /regulations/{id}/versions` | Library management |
| `POST /regulations/{id}/compare-versions` | Deterministic diff + candidate changes |
| `POST /obligations/extract` (`use_llm`) | Deterministic ground truth + optional bounded Gemini pass; returns `method`, `execution_mode`, `provider`, `warnings` |
| `POST /obligations/{id}/approve` | Human approval gate |
| `POST /mappings/suggest` (`use_ai`) | Candidates + optional Cerebras re-rank; `execution_mode` exposed |
| `POST /mappings/{id}/approve` | Human approval gate |
| `POST /evidence`, `POST /evidence/{id}/link`, `POST /evidence/{id}/review` | Evidence lifecycle |
| `POST /tests/run` | Design/operating tests |
| `POST /assessments/run` | Gap detection |
| `POST /risks/recalculate`, `GET /risks/summary` | Weighted risk scoring |
| `POST /remediation/generate` (`use_ai`) | Templated actions + optional AI drafts as labelled notes |
| `POST /remediation/{id}/approve`, `POST /remediation/{id}/complete` | Approval + evidence-gated closure |
| `POST /scenarios`, `POST /scenarios/{id}/simulate` | Portfolio optimisation |
| `GET /analysis/contradictions`, `GET /analysis/cross-regulation` | Cross-cutting analysis |
| `GET /agents/catalog`, `POST /agents/workflows/run` | Workflow catalogue + execution |

## 12. Observability & provider dashboard

`GET /system/status` (and `/system/providers`) report: Gemini/Cerebras availability + configured
model IDs, current execution mode, requests made/failed, token usage (where reported),
**locally-tracked estimated** Cerebras spend vs the application budget/daily/per-call limits,
cache entries/hit-rate, and recent fallback events. The UI (Settings + Dashboard strip) renders
all of this and states explicitly that the **provider dashboard is authoritative for the real
account balance** — the app never displays a fabricated remaining-credit figure.

## 13. Budget & privacy guards

- **~USD 4.00 of Cerebras credit is the hard constraint.** Pre-call estimates must fit the
  per-call (0.05), daily (1.00) and total (4.00) application guards or the call is blocked
  without touching the network; every completed call records tokens + estimated cost
  (official pricing: e.g. `gpt-oss-120b` $0.35/$0.75 per M in/out — re-verify in current docs);
  tripping the guard disables further Cerebras calls with a clear message. A meaningful reserve
  is preserved for the final demo: development defaults to **deterministic** (zero spend).
- **Privacy:** demo with synthetic/public documents only; no real bank, customer, credential or
  sensitive policy data goes to hosted APIs without explicit authorisation + data-handling
  review. Uploads, DB, audit and logs stay local. Keys live only in server `.env`, never in the
  browser, logs, or tests. Free-tier retention/data-use follows each provider's current published
  terms (surfaced in the UI, never assumed).

## 14. Testing

```powershell
cd backend
$env:TMP="C:\Users\dines\AppData\Local\Temp\opencode"; $env:TEMP=$env:TMP   # Windows TMP workaround if needed
python -m pytest tests/ -q          # full backend suite (SQLite-isolated)
python -m pytest tests/test_llm_providers.py -q   # 21 mocked provider tests, ZERO credits

cd ../frontend
npm test                            # vitest suite
npx tsc --noEmit                    # typecheck
```

`test_llm_providers.py` covers (all mocked): Gemini/Cerebras structured outputs, invalid-JSON
rejection, missing keys, timeouts, HTTP 429/quota, retry limits, fallback routing (+ “never
both on success”), budget pre-blocking/ledger/disabling, request caching, secret redaction,
deterministic mode, usage tracking, startup with neither provider, and honest
`/obligations/extract` fallback. Only deliberate live smoke tests (with keys configured, small
bounded prompts) ever touch real APIs — never the suite.

## 15. Evaluation dataset

`backend/eval/` holds a labelled evaluation dataset + runner (`run_eval.py`); the latest report
is `docs/EVAL_OUTPUT.json`. `docs/IMPLEMENTATION_PLAN.md` is the living build checklist,
including the Gemini+Cerebras architecture note.

## 16. Troubleshooting

| Symptom | Fix |
|---|---|
| `mode: deterministic` though keys set | Check `DETERMINISTIC_MODE=false`, `*_ENABLED=true`, keys non-empty; see `providers.*.reason` in `/system/status` |
| `Model 'x' not in account model list` | Model ID changed/retired — pick a currently available one, set `GEMINI_MODEL`/`CEREBRAS_MODEL` |
| `Cerebras calls disabled (budget guard)` | Application guard tripped — intended. Raise limits deliberately or wait for daily reset; check provider dashboard for the real balance |
| `use_llm=true` but `method: deterministic` | Expected when providers unavailable — read `warnings[]`; no output is fabricated |
| Postgres connection refused | `docker compose up -d db`, or `DATABASE_URL=sqlite:///./regulens.db` |
| OCR yields nothing | Install Tesseract binary + `pip install "regulens-ai[ocr]"` |
| Windows pytest `PermissionError` on temp | Set `TMP`/`TEMP` to a writable dir (see Testing) |
| Port clash (8000/5173/8080) | Change `BACKEND_PORT`/`FRONTEND_PORT` or Vite port |

---

*Built for the hackathon demo: deterministic by default, AI where it earns its place, honest
about everything else.*
