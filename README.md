# 🌿 Family AI Life OS

> **Production-Ready Multi-Agent Family Ecosystem for Denys & Oleksandra**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![aiogram 3](https://img.shields.io/badge/Telegram-aiogram%203-blue)](https://docs.aiogram.dev/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL%20%2B%20pgvector-blue)](https://www.postgresql.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Modular%20Monolith-green)](./CONTEXT.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

---

## 📌 Overview

**Family AI Life OS** is an intelligent, multi-agent assistant designed for **Denys** and **Oleksandra**. Instead of forcing users to manage separate apps, fill out complex spreadsheets, or toggle between specialized bots, **Family AI Life OS** provides a single natural Telegram interface.

Users simply write messages, upload photos of food or receipts, forward PDF lab reports, or record voice notes. The system automatically:

1. **Identifies the sender** (Denys or Oleksandra) and permission scope.
2. **Understands intent** via the **Main Orchestrator Agent**.
3. **Delegates tasks** to specialized agents (Health, Finance, Planner, Memory, Document, Notification).
4. **Executes deterministic tools** (PostgreSQL logging, Oura API sync, Google Sheets sync, S3 file storage).
5. **Enforces privacy rules** (personal data isolation vs. shared household space).
6. **Returns clear, actionable answers** in Telegram.

Voice notes and supported audio attachments are transcribed in memory with OpenAI,
then passed through the same identity, privacy, routing, and deterministic tool
checks as typed messages. Raw audio is not written to disk or stored by the app.

---

## 🏗 System Architecture

```text
Telegram (Personal Chats & Family Group)
   │
   ▼
API Gateway / Telegram Webhook (aiogram 3)
   │
   ▼
Identity & Permission Layer (Access Guard & Privacy Matrix)
   │
   ▼
Main Orchestrator Agent (Intent Recognition & Tool Router)
   │
   ├──────► Health Agent (Oura OAuth2 + Food Vision + Lab Metrics)
   ├──────► Finance Agent (Receipt OCR + Bank Statements + Google Sheets Sync)
   ├──────► Planner Agent (Tasks + Shopping Lists + Reminders + Calendar)
   ├──────► Memory Agent (Long-Term Semantic Vector Memory + Facts)
   ├──────► Document Agent (Medical & Financial File Parsing + S3 Metadata)
   └──────► Notification Agent (Digest Schedules + Personal Alerting)
   │
   ▼
Deterministic Tool Layer (Pydantic validated Python tools)
   │
   ├──────► PostgreSQL 16 + pgvector (Single Source of Truth)
   ├──────► Redis Queue & Outbox Worker (Async Tasks & Events)
   └──────► External Integrations (Oura API V2, Gemini API, Google Sheets API, S3)
```

---

## 🤖 Specialized Agents Overview

Detailed specifications for each agent are available in [AGENTS.md](./AGENTS.md).

- **Main Orchestrator**: Central routing coordinator. Parses intent, builds execution plans, invokes domain tools, and synthesizes answers.
- **Health Agent**: Manages personal Oura Ring OAuth2 integrations (Denys & Oleksandra), sleep/readiness/HRV trends, Gemini food photo macro analysis, and lab test tracking.
- **Finance Agent**: Processes expense receipts, bank statement exports, transaction categorization, household budget tracking, and real-time synchronization with Google Sheets.
- **Planner Agent**: Manages personal and shared tasks, shopping lists, calendar events, reminders, and travel preparation checklists.
- **Memory Agent**: Controls short-term conversational context and long-term semantic memory (pgvector), ensuring privacy and user data deletion requests ("forget this").
- **Document Agent**: Ingests PDFs, images, receipts, and medical reports. Extracts structured data, computes checksums, and uploads files to S3-compatible storage.
- **Notification Agent**: Handles scheduled personal reminders, shared household notifications, morning/evening digests, and quiet-hour compliance.

---

## 🛡 Security & Privacy Model

- **Data Isolation**: Denys cannot view Oleksandra's personal health metrics or medical reports without explicit authorization (`shared_with_partner` or `household` scope).
- **Group Chat Protection**: Sensitive personal information is never posted into the shared Telegram group without explicit consent.
- **OAuth2 Token Encryption**: All Oura OAuth access & refresh tokens are encrypted at rest using AES-256 GCM.
- **Audit Logging**: Every tool execution, state change, and permissions check is recorded in `audit_logs`.

---

## 🛠 Tech Stack

- **Core**: Python 3.12+, FastAPI, `aiogram` 3.x
- **Database**: PostgreSQL 16 with `pgvector`, SQLAlchemy 2.0 (Async), Alembic
- **Caching & Queue**: Redis, Outbox Worker Pattern
- **AI / LLM**: Provider Abstraction (`LLMProvider` for OpenAI & Google Gemini)
- **Integrations**: Oura API V2 (OAuth2), Google Sheets API, S3 Object Storage
- **Testing & Quality**: Pytest, Asyncio Test Suite, Ruff, Mypy

---

## 🚀 Quick Start

### 1. Environment Setup
Copy the example environment configuration:
```bash
cp .env.example .env
```
Fill in your credentials:
- `TELEGRAM_BOT_TOKEN`: From [@BotFather](https://t.me/BotFather)
- `GEMINI_API_KEY`: From Google AI Studio
- `OPENAI_API_KEY`: Used for natural responses and voice-note transcription
- `DATABASE_URL`: PostgreSQL connection string (`postgresql+asyncpg://...`)
- `OURA_CLIENT_ID` & `OURA_CLIENT_SECRET`: From Oura Developer Portal

### 2. Run with Docker Compose
```bash
docker-compose up -d --build
```

### 3. Run Database Migrations
```bash
docker-compose exec app alembic upgrade head
```

---

## 📄 Documentation Sitemap

- [AGENTS.md](./AGENTS.md): Comprehensive Agent Specifications & Tool Schemas.
- [CONTEXT.md](./CONTEXT.md): System Vision, Household Domain Model & Product Context.
- [REQ.md](./REQ.md): Functional, Technical & Security Requirements.
- [STATE.md](./STATE.md): Implementation State & Component Progress Tracking.
- [STATUS.md](./STATUS.md): High-Level Milestone Dashboard & Roadmap.

---

## 📜 License

Distributed under the MIT License. See [LICENSE](./LICENSE) for details.
