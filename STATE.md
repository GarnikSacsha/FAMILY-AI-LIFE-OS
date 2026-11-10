# 📊 Implementation State & System Matrix

Last reviewed: 2026-08-11

## Current system state

```text
[Phase 1: Root documentation] .......................... IMPLEMENTED (needs ongoing sync)
[Phase 2: Database models and migrations] .............. IMPLEMENTED
[Phase 3: Provider integrations] ....................... IMPLEMENTED (see limits below)
[Phase 4: Deterministic domain tools] .................. IMPLEMENTED
[Phase 5: Orchestrator and domain routing] ............. IMPLEMENTED
[Phase 6: Telegram interface and workers] .............. IMPLEMENTED
[Phase 7: Tests and Docker environment] ................ IMPLEMENTED
[Phase 8: Embeddings, pgvector retrieval, and RAG] ..... PLANNED
[Phase 9: S3 document pipeline and separate agents] .... PLANNED
```

## Component health matrix

| Component | Status | Evidence / scope |
| :--- | :--- | :--- |
| Root documentation | 🟢 Implemented | README, AGENTS, CONTEXT, REQ, STATE, STATUS, SECURITY, LICENSE |
| Database schema | 🟢 Implemented | SQLAlchemy models, PostgreSQL configuration, 13 Alembic revisions, identity bootstrap |
| Identity and authorization | 🟢 Implemented | Telegram allowlist, family-group ID guard, private-only sensitive domains, audit logging |
| Main orchestrator | 🟢 Implemented | Deterministic intent routing, confirmations, planner/finance/health/memory flows |
| Finance | 🟢 Implemented | Expense parsing, idempotency, confirmations, PostgreSQL persistence, Sheets projection |
| Planning | 🟢 Implemented | Tasks, multi-line task lists, shopping, reminders, calendar and recurring flows |
| Shared memory | 🟢 Implemented | Recent PostgreSQL context, structured facts/actions, summaries, digests, retry outbox |
| Oura | 🟢 Implemented | OAuth2 flow, encrypted tokens, refresh, allowlisted daily collections, health summaries |
| Google Workspace | 🟢 Implemented | OAuth2, Gmail, Calendar, and Sheets adapters with tests; live credentials are deployment-specific |
| Gemini / OpenAI input | 🟢 Implemented | Food-image analysis and in-memory audio transcription adapters with tests |
| Telegram bot | 🟢 Implemented | aiogram polling, private/group handlers, command menu, voice/photo/document paths |
| Background workers | 🟢 Implemented | Reminders, shared summaries/digests, Google Sheets sync, retry delivery |
| Docker and migrations | 🟢 Implemented | Dockerfile, Compose services, migration service, health/readiness endpoints |
| Automated tests | 🟢 Implemented | Latest local run: 290 passed, 1 skipped; Ruff passed |
| pgvector / vector memory | ⚪ Planned | No embedding model, vector column/index, or vector retrieval code exists yet |
| S3 document storage | ⚪ Planned | Configuration placeholders exist; finished upload/parse/index pipeline is absent |
| Separate specialized agents | ⚪ Planned | Current domain behavior lives in tools, orchestrator branches, and workers |

## Important scope note

“Implemented” means that the repository contains the corresponding application
path and automated coverage. It does not mean that every external provider is
currently connected in every deployment. Live Oura, Google, Gemini, OpenAI,
Telegram, database, Redis, and Sheets behavior depends on environment
configuration and provider availability.

The current shared memory is PostgreSQL-backed recent/structured context. It
must not be described as long-term semantic vector memory or RAG until an
embedding and vector-retrieval implementation is added and verified.
