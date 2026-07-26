import unittest

from app.security.oauth import OAuthStateManager


class TestOAuthState(unittest.TestCase):
    def test_state_hash(self):
        raw_state = "test_raw_state_12345"
        hash1 = OAuthStateManager._hash_state(raw_state)
        hash2 = OAuthStateManager._hash_state(raw_state)
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)  # SHA-256 hex digest length

    def test_empty_raw_state_hash(self):
        raw_state = ""
        hashed = OAuthStateManager._hash_state(raw_state)
        self.assertTrue(len(hashed) == 64)


if __name__ == "__main__":
    unittest.main()
