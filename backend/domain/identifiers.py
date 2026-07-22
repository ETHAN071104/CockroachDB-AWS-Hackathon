from __future__ import annotations

from uuid import UUID, uuid4, uuid5


AGENTBOOK_MIGRATION_NAMESPACE = UUID("7ec74ea0-3348-4d86-b8bc-58c0a60e7c86")
MAX_PUBLIC_ID = (1 << 63) - 1


def new_record_id() -> UUID:
    """Return an application-generated distributed primary key."""
    return uuid4()


def public_id_from_uuid(value: UUID) -> int:
    """Derive a stable, positive, non-sequential API integer from a UUID."""
    public_id = value.int & MAX_PUBLIC_ID
    return public_id or 1


def deterministic_legacy_uuid(
    workspace_id: str,
    source_table: str,
    legacy_id: int | str,
) -> UUID:
    """Map one legacy identity to the same UUID in dry runs and real imports."""
    canonical_workspace = str(UUID(str(workspace_id)))
    normalized_table = source_table.strip().lower()
    if not normalized_table:
        raise ValueError("Source table cannot be empty.")
    if isinstance(legacy_id, bool) or str(legacy_id).strip() == "":
        raise ValueError("Legacy ID cannot be empty.")
    return uuid5(
        AGENTBOOK_MIGRATION_NAMESPACE,
        f"{canonical_workspace}:{normalized_table}:{legacy_id}",
    )
