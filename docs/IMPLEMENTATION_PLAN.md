# ReguLens AI — Implementation Plan

> Living checklist. Updated as work completes. Status: `DONE` = implemented **and** verified by an executed test or run.

## 0. Environment findings (measured, not assumed)

| Item | Finding |
|---|---|
| Working dir | `C:\Users\dines\Downloads\ET_Hack` (empty repo, git-initialised under `C:\Users\dines`) |
| Python | 3.14.3 (`C:\Python314`), also 3.12 available via `py -3.12` |
| Node / npm | v24.14.0 / 10.3.0 |
| Docker | 28.5.1 (Docker Desktop), Compose v2.40.3 |
| PostgreSQL (host) | 18 running, `scram-sha-256`, password unknown → **not used for dev** |
| PostgreSQL (docker) | `pgvector/pgvector:pg17` verified: PostgreSQL 17.11 + `vector` 0.8.7 on port 55432 |
| RAM / CPU | 7.7 GB / 8 logical cores |
| Hosted AI | no keys by default → deterministic mode is the default; Gemini + Cerebras opt-in via `.env` |
| Network | PyPI + npm reachable |
| LAYA | **not installed and never required** |

### Dependency/licence checks

| Component | Verdict |
|---|---|
| Neuro SAN (`cognizant-ai-lab/neuro-san`) | Apache-2.0, PyPI `0.7.10`, resolves on Python 3.14 — **but** its dependency closure installs `openai` + `langchain-openai`, i.e. a commercial paid-API client. Conflicts with constraint 2.1 ("No OpenAI API dependency"). Decision: **not integrated**; orchestration lives behind an `OrchestrationBackend` interface with a native state-machine default and an optional adapter. Verified with `pip install --dry-run neuro-san`. |
| ortools | Apache-2.0, 9.15.6755, cp314 wheel installed |
| psycopg | LGPL-3.0 (psycopg) / LGPL-3.0 binary distribution — compatible, installed |
| pgvector | PostgreSQL License (PostgreSQL), used via docker image |
| pdfplumber / pypdf | MIT / BSD |
| scikit-learn | BSD-3-Clause |
| fastapi / uvicorn / sqlalchemy / pydantic | MIT / BSD / MIT |
| MUI (frontend) | MIT |

## Phase 1 — Foundation
- [x] Inspect repo + environment
- [x] Download real RBI Master Direction PDFs into `data/samples/official_rbi/`
- [x] Decide orchestration backend (native, interface-based)
- [ ] Project skeleton, config, logging, error handling
- [ ] `.env.example`, `docker-compose.yml`

## Phase 2 — Database & domain models
- [ ] SQLAlchemy models (all required entities)
- [ ] Alembic migration (dialect-aware: pgvector / plain text)
- [ ] Seed script

## Phase 3 — Ingestion
- [ ] Upload validation (type, size, path traversal, content sniffing)
- [ ] PDF / DOCX / TXT / HTML / CSV / JSON parsers
- [ ] Optional OCR hook (tesseract, degrades gracefully)
- [ ] Document + document_versions + chunks

## Phase 4 — Compliance engine
- [ ] Obligation extraction (deterministic + optional LLM)
- [ ] Version comparison / regulatory change detection
- [ ] Applicability rule engine
- [ ] Obligation→control mapping (hybrid retrieval)
- [ ] Evidence assessment state machine
- [ ] Control testing (design vs operating)
- [ ] Gap detection
- [ ] Risk scoring (transparent, configurable weights)

## Phase 5 — Orchestration
- [ ] `OrchestrationBackend` interface + native state machine
- [ ] 13 logical agents, typed IO, timeouts, bounded retries, correlation IDs
- [ ] Persisted `agent_runs` / `agent_events`

## Phase 6 — Hosted AI (Gemini + Cerebras, optional)
- [x] Provider-independent `LLMProvider` interface (`generate_text` / `generate_structured` / `health_check`)
- [x] `GeminiProvider` (official `google-genai` SDK, structured JSON via Pydantic, model verified via `models.list`)
- [x] `CerebrasProvider` (official `cerebras-cloud-sdk`, pre-call budget guard, usage + cost ledger)
- [x] `DeterministicProvider` (rule-based default; AI-only tasks return review-required, never fabricated)
- [x] Central `LLMRouter` (configurable routing, sequential fallback only, cache, concurrency cap, usage tracking)
- [x] Strict Cerebras guards: USD 4.00 application budget, USD 1.00/day, USD 0.05/call (application-level only)
- [x] `/system/status` + `/system/providers` observability; Settings & Dashboard provider sections
- [x] No local-LLM backend: no model downloads, no local weights, no LAYA dependency

## Phase 7 — Remediation & optimisation
- [ ] Remediation recommendation engine
- [ ] Dependencies
- [ ] CP-SAT portfolio optimiser + deterministic fallback
- [ ] 3 strategies

## Phase 8 — Frontend
- [ ] Vite + React + TS + MUI, 11 pages, real API data

## Phase 9 — Demo dataset
- [ ] Synthetic + real-RBI sample data, clearly labelled

## Phase 10 — Testing
- [ ] Backend pytest suite
- [ ] Frontend vitest suite
- [ ] E2E demo workflow test
- [ ] Evaluation dataset + report

## Phase 11 — Validation
- [ ] Full startup, demo run, API verification

## Architecture note — Gemini + Cerebras (mandatory correction, 2026-10-11)

- **No local-LLM backend.** All `OLLAMA_*` / `ENABLE_LOCAL_LLM` / `ENABLE_EMBEDDINGS` /
  `EMBEDDING_MODEL` settings, code paths, docs and UI references are removed. Retrieval vectors are
  deterministic hashed TF-IDF lexical vectors (labelled as such, never as neural embeddings).
- **Gemini** (`google-genai` SDK, `GEMINI_MODEL`, default `gemini-2.0-flash`, configurable): obligation
  extraction, document interpretation, version-comparison interpretation. Model verified via account
  model list before use is claimed; structured outputs validated with Pydantic; citations preserved.
- **Cerebras** (`cerebras-cloud-sdk`, `CEREBRAS_MODEL`, default `gpt-oss-120b`, configurable): fast
  mapping candidates, remediation drafts, concise analysis. Documented Developer-tier models
  (e.g. `gpt-oss-120b` $0.35/$0.75 per M in/out, `qwen-3.8-27b`-family, `gemma-4-31b`) verified against
  current official docs/pricing — never assumed.
- **Budget:** ~USD 4.00 remains. Application-level guards (`CEREBRAS_BUDGET_USD=4.00`,
  `CEREBRAS_DAILY_BUDGET_USD=1.00`, `CEREBRAS_MAX_CALL_COST_USD=0.05`, `CEREBRAS_MAX_RETRIES=2`) block
  calls before spend, record usage after every call, and disable further calls when tripped. These are
  NOT account-wide caps — the provider dashboard is authoritative.
- **Routing** lives only in `app/llm/router.py` (`DEFAULT_TASK_ROUTING` + `LLM_ROUTING_OVERRIDES`);
  deterministic-by-policy tasks (risk, deadlines, optimisation, citation checks, parsing) never reach
  a hosted model. Fallback is sequential (preferred → fallback → deterministic/review-required).
- **Privacy:** synthetic/public docs for the demo; keys server-side only (never to the browser, never
  logged); uploads/DB/audit stay local. Free-tier retention terms are the providers' current published
  terms, surfaced in the UI — not assumed.
- **Tests:** `backend/tests/test_llm_providers.py` (21 mocked tests, zero credits) + full suite + startup
  with neither provider enabled.
