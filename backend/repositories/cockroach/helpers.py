from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.domain import new_record_id, public_id_from_uuid
from backend.repositories.cockroach.connection import connection_scope


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def timestamp(value: str | datetime | None = None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def new_public_identity() -> tuple[UUID, int]:
    record_id = new_record_id()
    return record_id, public_id_from_uuid(record_id)


def uuid_for_public(table: str, workspace_id: str, public_id: int) -> UUID | None:
    if isinstance(public_id, bool) or int(public_id) <= 0:
        return None
    with connection_scope() as connection:
        value = connection.execute(
            text(
                f"SELECT id FROM {table} "
                "WHERE workspace_id = :workspace_id AND public_id = :public_id"
            ),
            {"workspace_id": workspace_id, "public_id": int(public_id)},
        ).scalar_one_or_none()
    return UUID(str(value)) if value is not None else None


def public_for_uuid(table: str, workspace_id: str, record_id: UUID | str) -> int | None:
    with connection_scope() as connection:
        value = connection.execute(
            text(
                f"SELECT public_id FROM {table} "
                "WHERE workspace_id = :workspace_id AND id = :record_id"
            ),
            {"workspace_id": workspace_id, "record_id": record_id},
        ).scalar_one_or_none()
    return int(value) if value is not None else None


def content_sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()
