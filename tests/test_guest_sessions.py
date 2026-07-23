from __future__ import annotations

import sqlite3
import tempfile
import unittest

from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.routes.guest_sessions import CREATION_LIMITER
from backend.application.dependencies import (
    configure_application_dependencies,
)
from backend.application.guest_sessions import (
    GuestSessionExpired,
    GuestSessionRevoked,
    GuestSessionService,
    TOKEN_PATTERN,
)
from backend.rag import config
from backend.rag import database as rag_database
from backend.repositories.sqlite import initialize_foundation_schema


TEST_PEPPER = "guest-session-test-pepper-with-at-least-32-bytes"


class GuestSessionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.database_path = Path(temporary) / "guest-sessions.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(patch.object(rag_database, "ensure_directories"))
        self.stack.enter_context(patch.object(config, "PERSISTENCE_BACKEND", "sqlite"))
        self.stack.enter_context(
            patch.object(config, "GUEST_SESSION_TOKEN_PEPPER", TEST_PEPPER)
        )
        self.stack.enter_context(
            patch.object(config, "ALLOW_LEGACY_DEFAULT_WORKSPACE", False)
        )
        rag_database.initialize_database()
        from backend.memory.database import initialize_memory_database
        from backend.study.database import initialize_study_database

        initialize_memory_database()
        initialize_study_database()
        initialize_foundation_schema()
        configure_application_dependencies(None)
        self.addCleanup(configure_application_dependencies, None)
        CREATION_LIMITER.clear_for_test()
        self.client = TestClient(
            create_app(allow_legacy_default_workspace=False),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def _create(self, key: str) -> tuple[str, dict[str, object]]:
        response = self.client.post(
            "/api/guest-session",
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        token = payload["token"]
        self.assertRegex(token, TOKEN_PATTERN)
        return token, payload

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_creation_stores_only_hmac_digests_and_inspection_is_safe(self) -> None:
        token, created = self._create("A" * 32)
        inspected = self.client.get(
            "/api/guest-session",
            headers=self._auth(token),
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertNotIn("token", inspected.json())
        self.assertNotIn("id", inspected.json()["workspace"])
        self.assertNotIn("workspace_id", inspected.text)
        self.assertNotIn("token_hash", inspected.text)

        with closing(sqlite3.connect(self.database_path)) as connection:
            stored = connection.execute(
                "SELECT token_hash, creation_key_hash FROM guest_sessions"
            ).fetchone()
        assert stored is not None
        self.assertEqual(len(stored[0]), 64)
        self.assertEqual(len(stored[1]), 64)
        self.assertNotEqual(stored[0], token)
        self.assertNotIn(token, str(created["session"]))

    def test_missing_invalid_url_and_workspace_override_are_rejected(self) -> None:
        missing = self.client.get("/api/notebooks")
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["error"]["code"], "GUEST_SESSION_REQUIRED")

        invalid = self.client.get(
            "/api/notebooks",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["error"]["code"], "GUEST_SESSION_INVALID")

        token, _ = self._create("B" * 32)
        query_token = self.client.get(
            f"/api/notebooks?access_token={token}",
        )
        self.assertEqual(query_token.status_code, 401)
        self.assertNotIn(token, query_token.text)
        override = self.client.get(
            "/api/notebooks?workspace_id=00000000-0000-4000-8000-000000000001",
            headers=self._auth(token),
        )
        self.assertEqual(override.status_code, 403)
        self.assertEqual(override.json()["error"]["code"], "WORKSPACE_ACCESS_DENIED")
        header_override = self.client.get(
            "/api/notebooks",
            headers={
                **self._auth(token),
                "X-Workspace-ID": "00000000-0000-4000-8000-000000000001",
            },
        )
        self.assertEqual(header_override.status_code, 403)
        body_override = self.client.post(
            "/api/notebooks",
            json={"name": "Denied", "workspace_id": "guessed"},
            headers=self._auth(token),
        )
        self.assertEqual(body_override.status_code, 422)

    def test_two_guests_are_relationally_isolated_and_survive_restart(self) -> None:
        token_a, _ = self._create("C" * 32)
        token_b, _ = self._create("D" * 32)

        notebook_a = self.client.post(
            "/api/notebooks",
            json={"name": "Guest A private notebook"},
            headers=self._auth(token_a),
        )
        self.assertEqual(notebook_a.status_code, 201, notebook_a.text)
        public_id = notebook_a.json()["id"]

        list_a = self.client.get("/api/notebooks", headers=self._auth(token_a))
        list_b = self.client.get("/api/notebooks", headers=self._auth(token_b))
        self.assertEqual(list_a.status_code, 200)
        self.assertEqual(list_b.status_code, 200)
        self.assertEqual([item["name"] for item in list_a.json()["items"]], [
            "Guest A private notebook"
        ])
        self.assertEqual(list_b.json()["items"], [])
        guessed = self.client.get(
            f"/api/notebooks/{public_id}",
            headers=self._auth(token_b),
        )
        self.assertEqual(guessed.status_code, 404)

        configure_application_dependencies(None)
        restarted_a = self.client.get(
            "/api/guest-session",
            headers=self._auth(token_a),
        )
        restarted_b = self.client.get(
            "/api/guest-session",
            headers=self._auth(token_b),
        )
        self.assertEqual(restarted_a.status_code, 200)
        self.assertEqual(restarted_b.status_code, 200)

    def test_idempotency_key_conflict_does_not_create_another_workspace(self) -> None:
        key = "E" * 32
        self._create(key)
        repeated = self.client.post(
            "/api/guest-session",
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["error"]["code"], "GUEST_SESSION_CONFLICT")
        with closing(sqlite3.connect(self.database_path)) as connection:
            workspace_count = connection.execute(
                "SELECT count(*) FROM workspaces"
            ).fetchone()[0]
            session_count = connection.execute(
                "SELECT count(*) FROM guest_sessions"
            ).fetchone()[0]
        self.assertEqual(session_count, 1)
        self.assertEqual(workspace_count, 2)

    def test_token_hash_uses_pepper_and_domain_separation(self) -> None:
        from backend.application.dependencies import get_application_dependencies

        service = GuestSessionService(
            get_application_dependencies(),
            pepper=TEST_PEPPER,
        )
        other = GuestSessionService(
            get_application_dependencies(),
            pepper=TEST_PEPPER + "-different",
        )
        token = "agentbook_guest_v1_" + ("x" * 43)
        self.assertNotEqual(
            service.hash_token_for_test(token),
            other.hash_token_for_test(token),
        )
        self.assertEqual(len(service.hash_token_for_test(token)), 64)

    def test_revoked_and_expired_sessions_fail_closed(self) -> None:
        from backend.application.dependencies import get_application_dependencies

        dependencies = get_application_dependencies()
        service = GuestSessionService(
            dependencies,
            pepper=TEST_PEPPER,
        )
        revoked = service.create("F" * 32)
        dependencies.guest_sessions.revoke(
            revoked.session.id,
            expected_version=revoked.session.version,
            revoked_at=revoked.session.created_at,
        )
        with self.assertRaises(GuestSessionRevoked):
            service.authenticate(revoked.token)

        expired = service.create("G" * 32)
        dependencies.guest_sessions.expire(
            expired.session.id,
            expected_version=expired.session.version,
            expired_at=expired.session.created_at,
        )
        with self.assertRaises(GuestSessionExpired):
            service.authenticate(expired.token)


if __name__ == "__main__":
    unittest.main()
