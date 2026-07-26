import base64
import binascii
import os
import uuid
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config.settings import settings


class TokenCipherError(Exception):
    """Base exception for token encryption/decryption failures."""

    pass


class TokenCipher:
    """Secure AES-256-GCM Token Encryption Cipher with Versioning and AAD."""

    VERSION_PREFIX = "v1"

    def __init__(self, key_base64: str | None = None):
        if not key_base64:
            raise TokenCipherError(
                "TOKEN_ENCRYPTION_KEY is required and cannot be empty. Provide a 32-byte Base64 encoded key."
            )

        try:
            raw_key = base64.b64decode(key_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TokenCipherError("Invalid Base64 format for TOKEN_ENCRYPTION_KEY.") from exc

        if len(raw_key) != 32:
            raise TokenCipherError(
                f"TOKEN_ENCRYPTION_KEY must be exactly 32 bytes (256 bits). Got {len(raw_key)} bytes."
            )

        self._aesgcm = AESGCM(raw_key)

    def _build_aad(self, user_id: uuid.UUID, provider: str, token_type: str) -> bytes:
        """Constructs Associated Authenticated Data (AAD) for AES-GCM integrity binding."""
        return f"{user_id}:{provider}:{token_type}".encode()

    def encrypt(self, plaintext: str, *, user_id: uuid.UUID, provider: str, token_type: str) -> str:
        """Encrypts plaintext string with unique 96-bit nonce and AAD binding."""
        if not plaintext or not plaintext.strip():
            raise TokenCipherError("Plaintext token cannot be empty.")

        nonce = os.urandom(12)  # 96-bit nonce
        aad = self._build_aad(user_id, provider, token_type)

        try:
            ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        except Exception as exc:
            raise TokenCipherError("Token encryption failed.") from exc

        nonce_b64 = base64.b64encode(nonce).decode("utf-8")
        ciphertext_b64 = base64.b64encode(ciphertext).decode("utf-8")

        return f"{self.VERSION_PREFIX}:{nonce_b64}:{ciphertext_b64}"

    def decrypt(self, payload: str, *, user_id: uuid.UUID, provider: str, token_type: str) -> str:
        """Decrypts versioned payload, verifying AAD integrity and nonce tag."""
        if not payload or not payload.startswith(f"{self.VERSION_PREFIX}:"):
            raise TokenCipherError("Invalid or unsupported ciphertext format.")

        parts = payload.split(":")
        if len(parts) != 3:
            raise TokenCipherError("Malformed token ciphertext payload.")

        _, nonce_b64, ciphertext_b64 = parts

        try:
            nonce = base64.b64decode(nonce_b64, validate=True)
            ciphertext = base64.b64decode(ciphertext_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TokenCipherError("Failed to decode ciphertext components.") from exc

        if len(nonce) != 12:
            raise TokenCipherError("Invalid AES-GCM nonce length.")

        aad = self._build_aad(user_id, provider, token_type)

        try:
            plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext_bytes.decode("utf-8")
        except Exception as exc:
            raise TokenCipherError("Token decryption failed integrity verification.") from exc


@lru_cache(maxsize=1)
def get_token_cipher() -> TokenCipher:
    """Build the configured application cipher without exposing key material."""
    key = settings.TOKEN_ENCRYPTION_KEY
    if key is None:
        raise TokenCipherError("TOKEN_ENCRYPTION_KEY is required.")
    return TokenCipher(key.get_secret_value())
