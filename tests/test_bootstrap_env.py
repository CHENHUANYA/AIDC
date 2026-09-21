import unittest
from unittest.mock import patch

from scripts import bootstrap_env


class BootstrapEnvTests(unittest.TestCase):
    def test_secret_updates_only_replaces_missing_or_placeholder_by_default(self):
        updates = bootstrap_env.secret_updates({
            "ADMIN_INITIAL_PASSWORD": "already-set",
            "ALARM_RAG_TRIGGER_TOKEN": "replace-with-a-random-trigger-token",
        })

        self.assertNotIn("ADMIN_INITIAL_PASSWORD", updates)
        self.assertIn("ALARM_RAG_TRIGGER_TOKEN", updates)
        self.assertIn("N8N_ENCRYPTION_KEY", updates)

    def test_secret_updates_rotate_replaces_existing_values(self):
        updates = bootstrap_env.secret_updates({
            "ADMIN_INITIAL_PASSWORD": "already-set",
            "ALARM_RAG_TRIGGER_TOKEN": "already-set",
            "N8N_ENCRYPTION_KEY": "already-set",
            "QDRANT_API_KEY": "already-set",
        }, rotate=True)

        self.assertEqual(set(bootstrap_env.PLACEHOLDERS), set(updates))
        self.assertNotEqual("already-set", updates["ADMIN_INITIAL_PASSWORD"])

    def test_reset_password_for_users_updates_accounts_and_revokes_sessions(self):
        users = {
            "operator01": {"user_id": "operator01", "password_hash": "old", "active": False},
            "admin01": {"user_id": "admin01", "password_hash": "old", "active": True},
        }
        saved = {}
        revoked = []

        def save_user(user_id, payload, expected_updated_at=None):
            del expected_updated_at
            saved[user_id] = dict(payload)
            return payload

        with (
            patch("auth.load_users", return_value=users),
            patch("auth.save_user", side_effect=save_user),
            patch("auth.hash_password", side_effect=lambda password: f"hash:{password}"),
            patch("auth.revoke_user_sessions", side_effect=lambda user_id: revoked.append(user_id)),
        ):
            updated = bootstrap_env.reset_password_for_users(
                ["operator01", "missing-user", "admin01"],
                "new-secret",
            )

        self.assertEqual(["operator01", "admin01"], updated)
        self.assertEqual("hash:new-secret", saved["operator01"]["password_hash"])
        self.assertEqual("hash:new-secret", saved["admin01"]["password_hash"])
        self.assertTrue(saved["operator01"]["active"])
        self.assertEqual(2, saved["operator01"]["credential_epoch"])
        self.assertEqual(["operator01", "admin01"], revoked)

    def test_reset_bootstrap_passwords_targets_seeded_role_accounts(self):
        updated_users = []

        with (
            patch("auth.BOOTSTRAP_USERS", {"operator01": {}, "maintenance01": {}}),
            patch.object(
                bootstrap_env,
                "reset_password_for_users",
                side_effect=lambda user_ids, password: updated_users.extend(user_ids) or user_ids,
            ),
        ):
            self.assertTrue(bootstrap_env.reset_bootstrap_passwords("new-secret"))

        self.assertEqual(["operator01", "maintenance01"], updated_users)


if __name__ == "__main__":
    unittest.main()
