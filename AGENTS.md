# 🤖 Specialized Agents Specification

This document details the multi-agent architecture for **Family AI Life OS**. All agents follow a strict deterministic execution pattern: agents use LLM reasoning solely to select actions and populate structured inputs, while all database mutations and external API calls occur via validated Python tools.

---

## 1. Main Orchestrator Agent (`Family Life Orchestrator`)

### Role
The central dispatching agent. Receives raw user messages (text, photos, documents, voice), extracts intent, evaluates access control, and constructs an execution plan.

### Responsibilities
1. Parse Telegram sender ID, chat type (private vs. family group), and timezone.
2. Resolve user permission scope (`private`, `shared_with_partner`, `household`).
3. Classify target domain(s): Health, Finance, Planner, Memory, Document, Notification.
4. Route sub-tasks to specialized domain tools.
5. Aggregate multi-domain results into a user-friendly, empathetic response.

### Input / Output Contract
```json
{
  "request_id": "uuid",
  "user_id": "uuid",
  "household_id": "uuid",
  "chat_type": "private | group",
  "intent": "HEALTH_QUERY | FINANCE_LOG | TASK_CREATE | DOCUMENT_PARSE | MULTI_DOMAIN",
  "target_agents": ["health", "planner"],
  "status": "SUCCESS | REQUIRES_CONFIRMATION | PERMISSION_DENIED | ERROR"
}
```

---

## 2. Health Agent (`Health Agent`)

### Role
Manages personal biometrics, Oura Ring synchronization, food nutrition vision, and medical records.

### Boundaries & Safeguards
- **Medical Disclaimer**: Does not provide medical diagnoses or alter prescribed treatment plans.
- **Data Isolation**: Maintains strictly separated Oura OAuth2 tokens for Denys and Oleksandra.
- **Nutrition Estimation**: Food photo vision provides realistic caloric/macro ranges, not false precision.

### Key Tools
- `connect_oura_account(user_id, code)`: Exchanges OAuth2 code for Oura tokens.
- `sync_oura_data(user_id, start_date, end_date)`: Pulls daily sleep, readiness, activity, SpO2, and HRV.
- `get_health_metrics(user_id, metrics, days)`: Fetches baseline trends and score deviations.
- `analyze_food_photo(file_id, user_id)`: Calls Gemini Vision model to estimate dish, ingredients, calories, and macros.
- `log_health_event(user_id, title, category, details)`: Logs symptoms, medications, or doctor visits.

---

## 3. Finance Agent (`Finance Agent`)

### Role
Manages income, expenses, receipts, bank exports, budgeting, and Google Sheets sync.

### Boundaries & Safeguards
- **Idempotency & Deduplication**: Prevents duplicate transaction logging via external IDs, transaction hashes, and merchant/amount matching.
- **Source of Truth**: PostgreSQL is the primary store; Google Sheets is a real-time synchronized view.
- **Privacy Gating**: Allows marking expenses as personal (private) or shared (household).

### Key Tools
- `save_financial_transaction(owner_type, owner_id, amount, currency, merchant, category)`: Logs expense or income.
- `categorize_transaction(description, merchant, amount)`: Uses ML/LLM heuristics to assign category.
- `import_bank_transactions(file_id, account_id)`: Parses CSV/PDF bank statements.
- `get_budget_summary(household_id, month)`: Calculates spending vs. budget limits.
- `sync_google_sheet(household_id)`: Pushes updated PostgreSQL transactions to Google Sheets.

---

## 4. Planner Agent (`Planner Agent`)

### Role
Manages task lists, shopping items, calendar appointments, and reminders.

### Key Tools
- `create_task(title, owner_type, owner_id, assignee_id, due_date)`: Creates a task.
- `add_shopping_item(item_name, household_id, quantity)`: Adds item to shared shopping list.
- `complete_shopping_item(item_id, user_id)`: Marks item as purchased.
- `create_calendar_event(title, start_time, end_time, owner_id)`: Schedules calendar event.
- `create_reminder(title, trigger_at, recipient_id)`: Sets timed notification.

---

## 5. Memory Agent (`Memory Agent`)

### Role
Manages short-term conversation context and long-term semantic facts (using `pgvector`).

### Safeguards
- Supports explicit user memory controls ("What do you remember about me?", "Forget this fact").
- Gated memory saving (filters sensitive medical/financial notes unless explicitly requested).

### Key Tools
- `save_semantic_fact(user_id, category, fact_text)`: Stores vector embedding of user preference or note.
- `search_memory(user_id, query)`: Retrieves relevant historical facts via vector similarity.
- `delete_memory(user_id, memory_id)`: Deletes specific stored fact.

---

## 6. Document Agent (`Document Agent`)

### Role
Ingests uploaded files (PDF, PNG, JPG), runs OCR/LLM parsing, categorizes documents, and stores raw files in S3.

### Key Tools
- `upload_document(file_id, owner_id, doc_type)`: Stores file in S3 and logs metadata in PostgreSQL.
- `process_document(file_id)`: Runs OCR and extracts key-value facts (lab results, receipts, warranties).
- `extract_lab_results(document_id)`: Parses test names, values, reference ranges, and lab flags.

---

## 7. Notification Agent (`Notification Agent`)

### Role
Schedules and delivers alerts, morning/evening digests, and event reminders.

### Key Tools
- `schedule_notification(recipient_id, message, trigger_at, quiet_hours_override)`: Schedules notification.
- `deliver_digest(user_id, digest_type)`: Sends morning readiness summary or evening budget log.
