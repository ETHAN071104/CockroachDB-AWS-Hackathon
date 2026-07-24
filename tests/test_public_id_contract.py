from __future__ import annotations

import unittest

from importlib import import_module
from typing import Annotated
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi import Path
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.export_service import _logical_export_value
from backend.api.public_ids import (
    MAX_JAVASCRIPT_SAFE_INTEGER,
    PublicId,
    PublicIdInput,
    parse_public_id,
)
from backend.api.schemas import (
    ApiModel,
    ChatRequest,
    DocumentResponse,
    LearningSignalResponse,
)
from backend.rag.notebooks import DocumentRecord
from backend.repositories.cockroach.helpers import new_public_identity


app_module = import_module("backend.api.app")


LARGE_PUBLIC_ID = 3_557_348_663_300_104_065
LARGE_PUBLIC_ID_TEXT = "3557348663300104065"
ROUNDED_PUBLIC_ID_TEXT = "3557348663300104000"
HEALTHY_STORAGE = {
    "document_vector_status": {
        "status": "ok",
        "collection_present": True,
    },
    "memory_vector_status": {
        "status": "ok",
        "collection_present": True,
    },
}


class PublicIdPayload(ApiModel):
    document_id: PublicIdInput


class PublicIdResponse(ApiModel):
    document_id: PublicId


class PublicIdContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.application = app_module.create_app(
            allow_legacy_default_workspace=True
        )

        @self.application.post(
            "/_test/public-id",
            response_model=PublicIdResponse,
        )
        def echo_public_id(payload: PublicIdPayload) -> PublicIdResponse:
            return PublicIdResponse(document_id=payload.document_id)

        @self.application.get(
            "/_test/public-id/{document_id}",
            response_model=PublicIdResponse,
        )
        def echo_public_path(
            document_id: Annotated[PublicIdInput, Path()],
        ) -> PublicIdResponse:
            return PublicIdResponse(document_id=document_id)

        storage_patch = patch.object(
            app_module,
            "initialize_storage",
            return_value=HEALTHY_STORAGE,
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def client(self) -> TestClient:
        return TestClient(self.application)

    def test_parser_accepts_canonical_decimal_strings_and_safe_numbers(self) -> None:
        self.assertEqual(parse_public_id(LARGE_PUBLIC_ID_TEXT), LARGE_PUBLIC_ID)
        self.assertEqual(parse_public_id("1"), 1)
        self.assertEqual(parse_public_id(MAX_JAVASCRIPT_SAFE_INTEGER), MAX_JAVASCRIPT_SAFE_INTEGER)

    def test_generated_id_can_exceed_javascript_safe_range_and_round_trip(self) -> None:
        record_id = UUID(int=LARGE_PUBLIC_ID)
        with patch(
            "backend.repositories.cockroach.helpers.new_record_id",
            return_value=record_id,
        ):
            generated_uuid, generated_public_id = new_public_identity()

        self.assertEqual(generated_uuid, record_id)
        self.assertGreater(
            generated_public_id,
            MAX_JAVASCRIPT_SAFE_INTEGER,
        )
        payload = PublicIdResponse(
            document_id=generated_public_id
        ).model_dump(mode="json")
        self.assertEqual(payload["document_id"], LARGE_PUBLIC_ID_TEXT)

    def test_parser_rejects_malformed_values(self) -> None:
        for value in (
            "",
            "0",
            "01",
            "-1",
            "+1",
            "1.0",
            "1e3",
            " 1",
            "1 ",
            True,
            0,
            -1,
            1.5,
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_public_id(value)

    def test_large_numeric_json_is_rejected_but_large_string_is_exact(self) -> None:
        with self.client() as client:
            accepted = client.post(
                "/_test/public-id",
                json={"document_id": LARGE_PUBLIC_ID_TEXT},
            )
            rejected = client.post(
                "/_test/public-id",
                json={"document_id": LARGE_PUBLIC_ID},
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(
            accepted.json(),
            {"document_id": LARGE_PUBLIC_ID_TEXT},
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(
            rejected.json()["error"]["code"],
            "PUBLIC_ID_STRING_REQUIRED",
        )
        error = rejected.json()["error"]
        self.assertEqual(error["title"], "This ID must be sent as text")
        self.assertEqual(
            error["reason"],
            "Large public IDs cannot be represented safely as JavaScript numbers.",
        )
        self.assertEqual(
            error["next_action"],
            "Send the complete decimal ID as a string.",
        )
        self.assertFalse(error["retryable"])
        self.assertRegex(error["request_id"], r"^[a-f0-9]{32}$")
        self.assertNotIn(LARGE_PUBLIC_ID_TEXT, rejected.text)

    def test_safe_numeric_json_is_accepted_and_response_is_a_string(self) -> None:
        with self.client() as client:
            response = client.post(
                "/_test/public-id",
                json={"document_id": 42},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"document_id": "42"})

    def test_malformed_body_and_path_use_structured_invalid_id_error(self) -> None:
        with self.client() as client:
            body_response = client.post(
                "/_test/public-id",
                json={"document_id": "01"},
            )
            path_response = client.get("/_test/public-id/1.5")

        for response in (body_response, path_response):
            self.assertEqual(response.status_code, 422)
            payload = response.json()["error"]
            self.assertEqual(payload["code"], "INVALID_PUBLIC_ID")
            self.assertEqual(payload["message"], "Request validation failed.")
            self.assertNotIn("input", payload["details"][0])

    def test_document_route_preserves_the_exact_19_digit_id(self) -> None:
        repository = Mock()
        repository.get_document.return_value = DocumentRecord(
            id=LARGE_PUBLIC_ID,
            filename="large-id.pdf",
            mime_type="application/pdf",
            file_hash="sha256:test",
            chunk_count=17,
            created_at="2026-07-24T00:00:00+00:00",
            updated_at="2026-07-24T00:00:00+00:00",
            notebook_id=None,
            notebook_name=None,
            assigned_at=None,
        )

        with (
            patch(
                "backend.api.routes.notebooks_documents._repository",
                return_value=repository,
            ),
            self.client() as client,
        ):
            response = client.get(
                f"/api/documents/{LARGE_PUBLIC_ID_TEXT}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], LARGE_PUBLIC_ID_TEXT)
        self.assertNotIn(ROUNDED_PUBLIC_ID_TEXT, response.text)
        repository.get_document.assert_called_once_with(LARGE_PUBLIC_ID)

    def test_document_response_and_nested_evidence_serialize_ids_as_strings(self) -> None:
        document = DocumentResponse(
            id=LARGE_PUBLIC_ID,
            filename="large-id.pdf",
            mime_type="application/pdf",
            chunk_count=1,
            created_at="2026-07-24T00:00:00+00:00",
            updated_at="2026-07-24T00:00:00+00:00",
            notebook_id=LARGE_PUBLIC_ID - 1,
        )
        signal = LearningSignalResponse(
            id="signal-1",
            source_type="quiz_attempt",
            source_id=LARGE_PUBLIC_ID_TEXT,
            source_question_id="2",
            topic="precision",
            signal_type="knowledge_gap",
            statement="Needs exact identifiers.",
            evidence=[
                {
                    "quiz_attempt_id": LARGE_PUBLIC_ID,
                    "question_number": 2,
                    "citations": [
                        {
                            "document_id": LARGE_PUBLIC_ID,
                            "chunk_index": 0,
                        }
                    ],
                }
            ],
            confidence=0.5,
            importance=0.5,
            occurrence_count=1,
            status="active",
            first_observed_at="2026-07-24T00:00:00+00:00",
            last_observed_at="2026-07-24T00:00:00+00:00",
            memory_id=LARGE_PUBLIC_ID - 2,
        )

        document_payload = document.model_dump(mode="json")
        signal_payload = signal.model_dump(mode="json")
        self.assertEqual(document_payload["id"], LARGE_PUBLIC_ID_TEXT)
        self.assertEqual(
            document_payload["notebook_id"],
            str(LARGE_PUBLIC_ID - 1),
        )
        self.assertEqual(
            signal_payload["memory_id"],
            str(LARGE_PUBLIC_ID - 2),
        )
        self.assertEqual(
            signal_payload["evidence"][0]["quiz_attempt_id"],
            LARGE_PUBLIC_ID_TEXT,
        )
        self.assertEqual(
            signal_payload["evidence"][0]["citations"][0]["document_id"],
            LARGE_PUBLIC_ID_TEXT,
        )
        self.assertEqual(
            signal_payload["evidence"][0]["question_number"],
            2,
        )

    def test_request_models_keep_exact_python_integers_internally(self) -> None:
        request = ChatRequest.model_validate(
            {
                "question": "Use this exact document.",
                "document_ids": [LARGE_PUBLIC_ID_TEXT],
            }
        )
        self.assertEqual(request.document_ids, [LARGE_PUBLIC_ID])

        with self.assertRaises(ValidationError) as raised:
            ChatRequest.model_validate(
                {
                    "question": "Reject a lossy JSON number.",
                    "document_ids": [LARGE_PUBLIC_ID],
                }
            )
        self.assertEqual(
            raised.exception.errors()[0]["type"],
            "public_id_string_required",
        )

    def test_logical_export_stringifies_ids_but_keeps_real_numbers(self) -> None:
        self.assertEqual(
            _logical_export_value(
                LARGE_PUBLIC_ID,
                key="public_id",
            ),
            LARGE_PUBLIC_ID_TEXT,
        )
        exported = _logical_export_value(
            {
                "document_id": LARGE_PUBLIC_ID,
                "chunk_index": 7,
                "score_percentage": 82.5,
            },
            key="source_snapshot",
        )
        self.assertEqual(exported["document_id"], LARGE_PUBLIC_ID_TEXT)
        self.assertEqual(exported["chunk_index"], 7)
        self.assertEqual(exported["score_percentage"], 82.5)

    def test_openapi_declares_public_ids_as_decimal_strings(self) -> None:
        schema = self.application.openapi()
        parameter = next(
            item
            for item in schema["paths"]["/api/documents/{document_id}"]["get"][
                "parameters"
            ]
            if item["name"] == "document_id"
        )
        self.assertEqual(parameter["schema"]["type"], "string")
        self.assertEqual(parameter["schema"]["pattern"], r"^[1-9][0-9]*$")
        self.assertIn(
            LARGE_PUBLIC_ID_TEXT,
            parameter["schema"]["examples"],
        )

        document_schema = schema["components"]["schemas"]["DocumentResponse"]
        self.assertEqual(document_schema["properties"]["id"]["type"], "string")
        self.assertEqual(
            document_schema["properties"]["id"]["pattern"],
            r"^[1-9][0-9]*$",
        )

    def test_openapi_contains_no_integer_public_id_parameters_or_fields(self) -> None:
        schema = self.application.openapi()

        def contains_integer(item: object) -> bool:
            if isinstance(item, list):
                return any(contains_integer(child) for child in item)
            if not isinstance(item, dict):
                return False
            if item.get("type") == "integer":
                return True
            return any(
                contains_integer(item.get(key))
                for key in ("items", "anyOf", "oneOf", "allOf")
            )

        violations: list[str] = []
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                for parameter in operation.get("parameters", []):
                    name = str(parameter.get("name", ""))
                    if (
                        name == "id"
                        or name.endswith("_id")
                        or name.endswith("_ids")
                    ) and contains_integer(parameter.get("schema")):
                        violations.append(f"{method.upper()} {path} {name}")

        for model_name, model in schema["components"]["schemas"].items():
            for field_name, field_schema in model.get("properties", {}).items():
                if (
                    field_name == "id"
                    or field_name.endswith("_id")
                    or field_name.endswith("_ids")
                ) and contains_integer(field_schema):
                    violations.append(f"{model_name}.{field_name}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
