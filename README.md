# 🌿 Family AI Life OS

> **Security-focused family assistant prototype for Denys & Oleksandra**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Telegram-aiogram%203-blue)](https://docs.aiogram.dev/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-blue)](https://www.postgresql.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Modular%20Monolith-green)](./CONTEXT.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

---

## 📌 What this repository is

Family AI Life OS is a private family assistant exposed through Telegram. It
combines a shared household space with private user-scoped workflows for
tasks, expenses, reminders, health data, mail, calendar events, and food or
voice input.

This repository contains a working prototype with real application code,
database migrations, background workers, provider adapters, Telegram handlers,
and an automated test suite. Production behavior still depends on deployment
configuration, credentials, external provider availability, and operational
smoke tests.

The implementation status is intentionally separated below so that the target
architecture is not confused with the current feature set.

## ✅ Implemented

- **Identity and privacy boundaries**: authorized Telegram users, a configured
  family group, private-only sensitive domains, encrypted OAuth tokens, audit
  records, and transaction-scoped database work.
- **Telegram interface**: aiogram polling, private/group routing, command menu,
  mention-aware commands such as `/tasks@familyhealtheee_bot`, voice messages,
  food photos, confirmations, and safe error handling.
- **Database layer**: SQLAlchemy models, Alembic migrations, PostgreSQL
  persistence, household/user ownership, finance, planning, reminders,
  confirmations, shared conversation context, summaries, and retry outbox.
- **Family planning**: tasks, multi-line task lists, shopping items, reminders,
  calendar requests, follow-up state, and recurring calendar flows.
- **Finance**: deterministic expense parsing and persistence with idempotency,
  categorization, confirmations, and Google Sheets projection/synchronization.
- **Health and input adapters**: Oura OAuth2 and daily collection client,
  Gemini food-image analysis, and in-memory OpenAI audio transcription.
- **Google Workspace**: OAuth2 flow plus Gmail and Google Calendar access in
  private chats; Google Sheets synchronization for household finance.
- **Shared family memory**: PostgreSQL-backed recent conversation context,
  explicit facts/actions/decisions, summaries, daily digests, and reminder
  follow-ups. This is structured/recent context, not vector RAG.
- **Operations and quality**: Docker Compose, Redis workers, `/live` and
  `/health` endpoints, pytest coverage, and Ruff checks.

## 🟡 In progress / needs production validation

- Live deployment runbooks, provider smoke tests, monitoring, and recovery
  procedures for Railway, Telegram, PostgreSQL, Redis, Oura, Google, Gemini,
  OpenAI, and Google Sheets.
- Consolidating the current tool-oriented domain modules into clearer product
  boundaries while keeping the existing security and data ownership rules.
- Documentation and configuration hardening for a repeatable production setup.

## 🗺 Planned, not implemented

- **pgvector embeddings, vector indexes, embedding generation, and vector
  retrieval/RAG**. The Docker Compose PostgreSQL image includes pgvector, but
  the application currently does not define an embedding schema or retrieval
  pipeline.
- **S3-compatible document storage and a complete Document Agent**. S3
  settings exist, but there is no finished upload/parse/index workflow.
- **Independent Health, Planner, Document, and Notification agent classes**.
  Their current capabilities are implemented as orchestrator branches, tools,
  and workers; the fully separated multi-agent target architecture is not yet
  present.
- Bank-statement ingestion, medical-document extraction, and cross-domain
  semantic analytics beyond the implemented deterministic workflows.

---

## 🏗 Current architecture

```text
Telegram (private chats and authorized family group)
   │
   ▼
aiogram polling + identity/privacy guard
   │
   ▼
Main Orchestrator + deterministic domain tools
   ├── Finance + Google Sheets
   ├── Planner + reminders + calendar
   ├── Oura + health tools
   ├── Shared PostgreSQL conversation context
   ├── Gemini food-image analysis
   └── OpenAI audio transcription
   │
   ▼
PostgreSQL + Redis workers + OAuth provider integrations
```

The target architecture may later add vector retrieval, S3-backed documents,
and separately deployable/specialized agents. Those are roadmap items, not
current implementation claims.

## 🛡 Security and privacy model

- Only the configured Denys and Oleksandra Telegram identities are accepted.
- Group messages are accepted only from the configured family group.
- Health, OAuth, mail, calendar, and medical-document operations are private
  chat workflows unless explicitly designed otherwise.
- Personal data is separated from household-owned data at the database and
  orchestration layers.
- OAuth tokens are encrypted at rest with AES-256-GCM.
- Incoming updates run inside transaction boundaries with rollback on failure.

See [SECURITY.md](./SECURITY.md) for the detailed hardening model.

## 🛠 Tech stack

- **Core**: Python 3.12+, FastAPI, aiogram 3.x
- **Database**: PostgreSQL 16, SQLAlchemy 2.0, Alembic
- **Caching/workers**: Redis and retry/outbox workers
- **AI/input providers**: Google Gemini, OpenAI transcription, Oura API V2
- **Google integrations**: Gmail, Calendar, and Sheets APIs
- **Testing/quality**: Pytest, pytest-asyncio, Ruff, Mypy configuration
- **Not yet active**: pgvector retrieval/RAG and S3 document storage pipeline

## 🚀 Quick start

### 1. Environment setup

Copy the example configuration and fill it with local values. Never commit the
real `.env` file.

```bash
cp .env.example .env
```

Important values include `TELEGRAM_BOT_TOKEN`, authorized Telegram IDs,
`FAMILY_GROUP_CHAT_ID`, database/Redis credentials, and the provider OAuth/API
credentials required for the workflows you want to run.

### 2. Run with Docker Compose

```bash
docker-compose up -d --build
```

### 3. Run database migrations

```bash
docker-compose exec app alembic upgrade head
```

## Planner controls and health history

- `/tasks` lists active household tasks in pages of eight. Use ✓ to complete,
  ✏️ to rename, or 📅 to change the due date. For edits, use Telegram's **Reply**
  on the bot's prompt and send the new value. Dates use `DD.MM.YYYY HH:MM` in
  your account timezone; `-` removes the due date. The prompt has a Cancel button.
- `/shopping` lists household purchases with **Куплено** buttons. Completed
  records remain in the database and disappear from the active list. Existing
  task and shopping creation messages still work.
- The menu has two health entries: `/weekly_health` and `/monthly_health`.
  `/health` remains available by typing it; `/health week` and `/health month`
  remain compatible aliases. All health commands require a private chat.
- Health requests synchronize up to 90 completed days plus today, using seven
  Oura collections: daily sleep, readiness, activity, detailed sleep, SpO2,
  stress and resilience. Repeated requests within five minutes reuse a successful
  sync. There is no automatic delivery or background polling.
- The existing `oura_daily_metrics` table stores one row per user/day. Provider
  snapshots and derived daily summaries are AES-GCM encrypted in `raw_json`,
  bound to the user and date; the existing encryption key must remain available.
  Historical rows are retained beyond the 90-day refresh window. Empty or failed
  collections do not erase saved readings. A provider outage falls back to saved
  history with a freshness warning. No new migrations or OAuth scopes are added.
- Weekly/monthly comparisons exclude today, show coverage per metric, and use
  at least 70% coverage in both periods for the short comparison text. Daily
  commentary compares three consecutive days with the preceding 30-day baseline
  (at least 14 available days). Watch flags use application heuristics: sleep
  45 minutes shorter, minimum sleeping pulse 5 bpm higher, or HRV 20% lower.
  These are descriptive, non-clinical rules, not diagnoses or medical cutoffs.
  No health data is sent to an LLM for these summaries.

## 📄 Documentation sitemap

- [AGENTS.md](./AGENTS.md): domain and agent contracts.
- [CONTEXT.md](./CONTEXT.md): product vision and household model.
- [REQ.md](./REQ.md): functional, technical, and security requirements.
- [STATE.md](./STATE.md): evidence-based implementation matrix.
- [STATUS.md](./STATUS.md): milestone dashboard and roadmap.
- [SECURITY.md](./SECURITY.md): security and privacy controls.

## 📜 License

Distributed under the MIT License. See [LICENSE](./LICENSE) for details.
