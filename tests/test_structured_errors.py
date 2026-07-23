from __future__ import annotations

import sqlite3
import unittest

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.error_catalog import ERROR_CATALOG
from backend.api.errors import ApiError, install_error_handlers, map_exception


REQUIRED_CODES = {
    "NO_INDEXED_DOCUMENTS",
    "SCOPE_NOT_FOUND",
    "SCOPE_EMPTY",
    "DOCUMENT_NOT_READY",
    "NO_RELEVANT_CHUNKS",
    "INSUFFICIENT_GROUNDED_EVIDENCE",
    "CITATION_VALIDATION_FAILED",
    "NO_LEARNING_HISTORY",
    "NO_WEAKNESS_EVIDENCE",
    "NO_COACHING_ITEMS",
    "NO_STUDY_PLAN_INPUTS",
    "AI_PROVIDER_UNAVAILABLE",
    "AI_PROVIDER_RATE_LIMITED",
    "AI_PROVIDER_TIMEOUT",
    "AI_EMPTY_RESPONSE",
    "AI_INVALID_JSON",
    "AI_SCHEMA_VALIDATION_FAILED",
    "AI_REFUSAL",
    "AI_CONTEXT_TOO_LARGE",
    "DATABASE_UNAVAILABLE",
    "DATABASE_TRANSACTION_RETRY_EXHAUSTED",
    "VECTOR_RETRIEVAL_FAILED",
    "EMBEDDING_JOB_FAILED",
    "WORKSPACE_ACCESS_DENIED",
    "VALIDATION_ERROR",
    "REQUEST_CONFLICT",
    "INTERNAL_ERROR",
}


class FakeProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class StructuredErrorMappingTest(unittest.TestCase):
    def test_required_catalog_codes_are_complete_and_actionable(self) -> None:
        self.assertTrue(REQUIRED_CODES.issubset(ERROR_CATALOG))
        for code in REQUIRED_CODES:
            with self.subTest(code=code):
                definition = ERROR_CATALOG[code]
                self.assertTrue(definition.title)
                self.assertTrue(definition.reason)
                self.assertTrue(definition.next_action)
                self.assertIsInstance(definition.retryable, bool)
                self.assertIn(
                    definition.status_code,
                    {400, 403, 404, 409, 413, 422, 429, 500, 502, 503, 504},
                )

    def test_provider_rate_limit_timeout_and_unavailable_mapping(self) -> None:
        cases = (
            (
                FakeProviderError("provider detail", 429),
                "AI_PROVIDER_RATE_LIMITED",
                429,
                True,
            ),
            (
                TimeoutError("provider timed out"),
                "AI_PROVIDER_TIMEOUT",
                504,
                True,
            ),
            (
                FakeProviderError("provider service unavailable", 503),
                "AI_PROVIDER_UNAVAILABLE",
                503,
                True,
            ),
            (
                FakeProviderError("invalid configured credential", 401),
                "AI_PROVIDER_UNAVAILABLE",
                503,
                False,
            ),
        )
        for error, code, status, retryable in cases:
            with self.subTest(code=code, status=status):
                mapped = map_exception(error, context="coaching_generation")
                self.assertEqual(mapped.code, code)
                self.assertEqual(mapped.status_code, status)
                self.assertEqual(mapped.retryable, retryable)

    def test_structured_output_and_citation_mapping(self) -> None:
        cases = (
            (
                RuntimeError("The coaching model returned an empty response."),
                "AI_EMPTY_RESPONSE",
            ),
            (
                RuntimeError("The coaching model returned invalid JSON."),
                "AI_INVALID_JSON",
            ),
            (
                RuntimeError(
                    "Generated coaching activity contains empty required fields."
                ),
                "AI_SCHEMA_VALIDATION_FAILED",
            ),
            (
                RuntimeError(
                    "Generated coaching activity listed sources without visible citations."
                ),
                "CITATION_VALIDATION_FAILED",
            ),
            (
                RuntimeError("The provider returned a refusal."),
                "AI_REFUSAL",
            ),
            (
                RuntimeError("Maximum context length exceeded."),
                "AI_CONTEXT_TOO_LARGE",
            ),
        )
        for error, code in cases:
            with self.subTest(code=code):
                self.assertEqual(
                    map_exception(
                        error,
                        fallback_code="AI_SCHEMA_VALIDATION_FAILED",
                        context="coaching_generation",
                    ).code,
                    code,
                )

    def test_persistence_vector_and_unknown_mapping(self) -> None:
        database = map_exception(sqlite3.OperationalError("database is offline"))
        self.assertEqual(database.code, "DATABASE_UNAVAILABLE")
        self.assertEqual(database.status_code, 503)
        self.assertTrue(database.retryable)

        transaction = map_exception(
            RuntimeError("Transaction retries were exhausted.")
        )
        self.assertEqual(
            transaction.code,
            "DATABASE_TRANSACTION_RETRY_EXHAUSTED",
        )

        vector = map_exception(
            RuntimeError("adapter failed"),
            context="vector_retrieval",
        )
        self.assertEqual(vector.code, "VECTOR_RETRIEVAL_FAILED")

        embedding = map_exception(
            RuntimeError("worker failed"),
            context="embedding_job",
        )
        self.assertEqual(embedding.code, "EMBEDDING_JOB_FAILED")

        workspace = map_exception(
            PermissionError("Workspace access denied.")
        )
        self.assertEqual(workspace.code, "WORKSPACE_ACCESS_DENIED")

        unknown = map_exception(RuntimeError("private filesystem detail"))
        self.assertEqual(unknown.code, "INTERNAL_ERROR")
        self.assertFalse(unknown.retryable)


class StructuredErrorEnvelopeTest(unittest.TestCase):
    def setUp(self) -> None:
        application = FastAPI()
        install_error_handlers(application)

        @application.get("/ok")
        def ok() -> dict[str, bool]:
            return {"ok": True}

        @application.get("/rate-limit")
        def rate_limit() -> None:
            raise map_exception(
                FakeProviderError("provider-private-payload", 429),
                context="coaching_generation",
            )

        @application.get("/secret")
        def secret() -> None:
            raise ApiError(
                status_code=500,
                code="INTERNAL_ERROR",
                details={
                    "api_key": "synthetic-private-value",
                    "database_url": "synthetic-private-database-setting",
                    "note": "Bearer private-token",
                },
            )

        @application.get("/unknown")
        def unknown() -> None:
            raise RuntimeError(
                "secret filesystem path and raw private document content"
            )

        self.application = application

    def test_success_and_errors_include_one_safe_request_id(self) -> None:
        with TestClient(self.application, raise_server_exceptions=False) as client:
            success = client.get("/ok")
            failed = client.get("/rate-limit")

        self.assertRegex(success.headers["x-request-id"], r"^[a-f0-9]{32}$")
        payload = failed.json()["error"]
        self.assertEqual(failed.status_code, 429)
        self.assertEqual(payload["code"], "AI_PROVIDER_RATE_LIMITED")
        self.assertTrue(payload["retryable"])
        self.assertRegex(payload["request_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(
            failed.headers["x-request-id"],
            payload["request_id"],
        )
        self.assertNotIn("provider-private-payload", failed.text)

    def test_details_and_unknown_errors_do_not_leak_secrets(self) -> None:
        with (
            patch("backend.api.errors.LOGGER.error") as log_error,
            TestClient(
                self.application,
                raise_server_exceptions=False,
            ) as client,
        ):
            secret = client.get("/secret")
            unknown = client.get("/unknown")

        self.assertNotIn("sk-private-value", secret.text)
        self.assertNotIn("password@host", secret.text)
        self.assertNotIn("private-token", secret.text)
        self.assertEqual(
            secret.json()["error"]["details"]["api_key"],
            "[redacted]",
        )
        self.assertEqual(unknown.json()["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret filesystem", unknown.text)
        self.assertNotIn("private document", unknown.text)
        self.assertNotIn("Traceback", unknown.text)
        rendered_log_arguments = " ".join(
            str(argument)
            for call in log_error.call_args_list
            for argument in call.args
        )
        self.assertNotIn("secret filesystem", rendered_log_arguments)
        self.assertNotIn("private document", rendered_log_arguments)
        self.assertNotIn("sk-private-value", rendered_log_arguments)


if __name__ == "__main__":
    unittest.main()
