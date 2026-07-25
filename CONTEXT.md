# 🧠 Product Context & Vision: Family AI Life OS

## 🎯 Product Purpose

**Family AI Life OS** is built specifically for **Denys** and **Oleksandra**. It replaces fragmented apps (notion, spreadsheets, separate habit trackers, generic chat bots) with a single intelligent assistant living inside Telegram.

---

## 👥 Users & Household Domain Model

```text
Household: Denys & Oleksandra
├── User 1: Denys (Telegram ID, Timezone, Oura Connection, Personal Data)
└── User 2: Oleksandra (Telegram ID, Timezone, Oura Connection, Personal Data)
```

### Data Ownership Rules
Every entity stored in PostgreSQL belongs to either:
1. `owner_type: user`, `owner_id: user_id` (Private to Denys or Oleksandra)
2. `owner_type: household`, `owner_id: household_id` (Shared family space)

---

## 💡 Key User Workflows

1. **Morning Health & Readiness Summary**:
   - Oura API automatically syncs sleep, HRV, readiness scores for both users.
   - Bot sends personal morning digest in Telegram private chats.

2. **Food Photo Macro Logging**:
   - User snaps photo of lunch -> sends to Telegram bot.
   - Gemini Vision analyzes dish, estimates calories/macros range.
   - User confirms with inline button -> saved to `meals` database table.

3. **Receipt & Expense Management**:
   - User snaps photo of store receipt or forwards PDF statement.
   - Document Agent parses items, Finance Agent categorizes expenses.
   - Automatically appended to PostgreSQL and synced to family Google Sheet.

4. **Shared Family Planning**:
   - "Add cat food to shopping list" -> Planner Agent adds item to household list.
   - "Remind us 2 days before trip to pack first-aid kit" -> Notification Agent schedules alert for both users.

5. **Cross-Domain Health & Finance Analysis**:
   - "How much did we spend on health this month and did it correlate with doctor visits?" -> Orchestrator combines Finance, Document, and Health Agent outputs into a unified answer.
