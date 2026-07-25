# 🔒 Security & Hardening Model (SECURITY.md)

## 1. OAuth Token Encryption (`TokenCipher`)

- **Algorithm**: AES-256-GCM using Python `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
- **Key Requirement**: Requires `TOKEN_ENCRYPTION_KEY` (32 bytes Base64). Fail-closed application startup if missing or invalid.
- **Nonce Generation**: 96-bit (12-byte) cryptographically secure random nonce generated per operation (`os.urandom(12)`).
- **Associated Authenticated Data (AAD)**: Integrity bound to `f"{user_id}:{provider}:{token_type}"`.
- **Versioned Format**: `v1:<base64_nonce>:<base64_ciphertext>`.

---

## 2. Identity & Access Guard (`IdentityService`)

- **Actor Context Resolution**: Maps Telegram User ID (`BigInteger`) strictly to internal DB `UUID` (`user_id` and `household_id`).
- **Allowlist Guard**: Only authorized Telegram IDs (Denys & Oleksandra) can communicate with the bot.
- **Chat Policy Enforcement**:
  - `private` chats: Restricted strictly to registered Denys & Oleksandra IDs.
  - `group`/`supergroup`: Restricted strictly to matching `FAMILY_GROUP_CHAT_ID`.
  - Sensitive domains (`health`, `oauth`, `medical_docs`, `personal_memory`): Enforced to operate **exclusively in private 1-on-1 chats**.

---

## 3. Secure OAuth2 PKCE & Webhook Callback (`OAuthStateManager`)

- **State Entropy**: 256-bit cryptographically secure token (`secrets.token_urlsafe(32)`).
- **State Storage**: Stored in PostgreSQL as SHA-256 state hashes with 10-minute TTL. Single-use enforcement (`consumed_at` tracking).
- **HTTP Callback**: Served at `GET /oauth/oura/callback` returning clean, neutral HTML without exposing authorization codes or tokens to client URLs.

---

## 4. Transaction Boundaries & Data Integrity

- **Unit of Work**: Single transaction boundary per incoming update via `AsyncSessionLocal.begin()`.
- **No Premature Commits**: Domain tools use `session.flush()` for entity ID retrieval and never execute `commit()`.
- **Automatic Rollback**: Any uncaught exception triggers complete session rollback.
- **Monetary Precision**: All financial amounts utilize Python `Decimal` / PostgreSQL `Numeric(12, 2)`. Date boundaries use half-open intervals `[date_from, date_to)`.
