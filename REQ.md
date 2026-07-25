# 📋 System Requirements Specification (REQ.md)

## 1. Functional Requirements

### 1.1 Identity & User Management
- **REQ-ID-1**: Identify users by unique Telegram User ID (Denys & Oleksandra).
- **REQ-ID-2**: Support single household workspace with granular privacy boundaries (`private`, `shared_with_partner`, `household`).

### 1.2 Telegram Interface
- **REQ-TG-1**: Handle incoming text, photos, PDFs, documents, voice notes, and location.
- **REQ-TG-2**: Provide fallback `/slash` commands (`/start`, `/help`, `/tasks`, `/health`, `/budget`, `/settings`).
- **REQ-TG-3**: Operate seamlessly in 1-on-1 private chats and family group chat.

### 1.3 Health & Biometrics Module
- **REQ-HL-1**: Separate Oura OAuth2 token storage for Denys and Oleksandra.
- **REQ-HL-2**: Pull daily sleep, readiness, HRV, SpO2, and activity metrics.
- **REQ-HL-3**: Analyze food photos using LLM Vision model (Gemini 2.5 Flash), outputting structured calorie and macro estimates.
- **REQ-HL-4**: Parse medical laboratory test reports (PDF/images) into structured metric records.

### 1.4 Financial Module
- **REQ-FN-1**: Ingest receipts, CSV/PDF bank statements, and manual expense entries.
- **REQ-FN-2**: Categorize expenses automatically with user override learning.
- **REQ-FN-3**: Maintain robust deduplication across transactions.
- **REQ-FN-4**: Sync transactions to Google Sheets in real-time while keeping PostgreSQL as primary source of truth.

### 1.5 Planner & Memory Modules
- **REQ-PL-1**: Support tasks, shopping lists, calendar events, and scheduled reminders.
- **REQ-MM-1**: Long-term semantic memory storage using `pgvector`.
- **REQ-MM-2**: Allow users to query and delete stored memory facts ("What do you remember?", "Forget this").

---

## 2. Non-Functional & Technical Requirements

- **REQ-NF-1**: **Performance**: Average response latency < 3.0s for text intent routing.
- **REQ-NF-2**: **Reliability**: Outbox pattern for message queue processing to ensure zero transaction data loss.
- **REQ-NF-3**: **Security**: AES-256 GCM encryption for stored OAuth tokens.
- **REQ-NF-4**: **Maintainability**: Modular Monolith design pattern allowing seamless future microservice separation.
