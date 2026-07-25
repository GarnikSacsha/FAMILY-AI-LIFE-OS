import base64
import os
import unittest
import uuid
from app.security.token_cipher import TokenCipher, TokenCipherError


class TestTokenCipher(unittest.TestCase):

    def setUp(self):
        # Generate a valid 32-byte Base64 key
        self.raw_key = os.urandom(32)
        self.valid_key_b64 = base64.b64encode(self.raw_key).decode("utf-8")
        self.cipher = TokenCipher(self.valid_key_b64)
        self.user_id = uuid.uuid4()
        self.provider = "oura"
        self.token_type = "access_token"

    def test_missing_or_invalid_key(self):
        with self.assertRaises(TokenCipherError):
            TokenCipher(None)
        with self.assertRaises(TokenCipherError):
            TokenCipher("")
        with self.assertRaises(TokenCipherError):
            # Short key (16 bytes)
            TokenCipher(base64.b64encode(b"shortkey12345678").decode("utf-8"))

    def test_encrypt_decrypt_success(self):
        token_str = "secret-oura-access-token-12345"
        encrypted = self.cipher.encrypt(
            token_str, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )
        self.assertTrue(encrypted.startswith("v1:"))

        decrypted = self.cipher.decrypt(
            encrypted, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )
        self.assertEqual(decrypted, token_str)

    def test_unique_nonce_per_encryption(self):
        token_str = "secret-token"
        enc1 = self.cipher.encrypt(
            token_str, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )
        enc2 = self.cipher.encrypt(
            token_str, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )
        # Same plaintext should yield different ciphertexts (due to unique random nonce)
        self.assertNotEqual(enc1, enc2)

    def test_aad_tamper_detection(self):
        token_str = "secret-token"
        encrypted = self.cipher.encrypt(
            token_str, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )

        # Attempt decryption with wrong user_id (AAD mismatch)
        other_user = uuid.uuid4()
        with self.assertRaises(TokenCipherError):
            self.cipher.decrypt(
                encrypted, user_id=other_user, provider=self.provider, token_type=self.token_type
            )

        # Attempt decryption with wrong token_type (AAD mismatch)
        with self.assertRaises(TokenCipherError):
            self.cipher.decrypt(
                encrypted, user_id=self.user_id, provider=self.provider, token_type="refresh_token"
            )

    def test_ciphertext_tamper_detection(self):
        token_str = "secret-token"
        encrypted = self.cipher.encrypt(
            token_str, user_id=self.user_id, provider=self.provider, token_type=self.token_type
        )
        # Mutate last character of payload
        tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")

        with self.assertRaises(TokenCipherError):
            self.cipher.decrypt(
                tampered, user_id=self.user_id, provider=self.provider, token_type=self.token_type
            )


if __name__ == "__main__":
    unittest.main()
