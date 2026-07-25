import unittest
import uuid
from app.domains.identity.service import ActorContext, IdentityService, PermissionDeniedError


class TestIdentityService(unittest.TestCase):

    def setUp(self):
        self.user_id = uuid.uuid4()
        self.household_id = uuid.uuid4()
        self.actor = ActorContext(
            user_id=self.user_id,
            telegram_id=123456789,
            household_id=self.household_id,
            chat_id=123456789,
            chat_type="private",
            is_admin=True,
        )

    def test_domain_access_private(self):
        # Private chat allows health, oauth, medical_docs
        try:
            IdentityService.validate_domain_access(self.actor, "health")
            IdentityService.validate_domain_access(self.actor, "oauth")
            IdentityService.validate_domain_access(self.actor, "finance")
        except PermissionDeniedError:
            self.fail("validate_domain_access raised PermissionDeniedError unexpectedly!")

    def test_domain_access_group_restriction(self):
        group_actor = ActorContext(
            user_id=self.user_id,
            telegram_id=123456789,
            household_id=self.household_id,
            chat_id=-100123456789,
            chat_type="group",
            is_admin=False,
        )
        # Group chat blocks sensitive domains
        with self.assertRaises(PermissionDeniedError):
            IdentityService.validate_domain_access(group_actor, "health")

        with self.assertRaises(PermissionDeniedError):
            IdentityService.validate_domain_access(group_actor, "oauth")

        # Group chat allows non-sensitive domains (e.g., finance / planning)
        try:
            IdentityService.validate_domain_access(group_actor, "finance")
            IdentityService.validate_domain_access(group_actor, "planner")
        except PermissionDeniedError:
            self.fail("Group chat should allow finance and planner domains.")


if __name__ == "__main__":
    unittest.main()
