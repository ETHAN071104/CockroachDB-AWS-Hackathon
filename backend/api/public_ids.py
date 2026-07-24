from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BeforeValidator,
    PlainSerializer,
    WithJsonSchema,
)
from pydantic_core import PydanticCustomError


PUBLIC_ID_PATTERN = r"^[1-9][0-9]*$"
PUBLIC_ID_EXAMPLE = "3557348663300104065"
MAX_JAVASCRIPT_SAFE_INTEGER = (1 << 53) - 1
MAX_INT8 = (1 << 63) - 1

_PUBLIC_ID_RE = re.compile(PUBLIC_ID_PATTERN)
_PUBLIC_ID_JSON_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": PUBLIC_ID_PATTERN,
    "examples": [PUBLIC_ID_EXAMPLE],
}


def _invalid_public_id() -> PydanticCustomError:
    return PydanticCustomError(
        "invalid_public_id",
        "Public IDs must be positive decimal strings.",
    )


def _public_id_string_required() -> PydanticCustomError:
    return PydanticCustomError(
        "public_id_string_required",
        "Large public IDs must be sent as decimal strings.",
    )


def _validate_range(value: int) -> int:
    if value < 1 or value > MAX_INT8:
        raise _invalid_public_id()
    return value


def parse_public_id(value: object) -> int:
    """Parse an API public ID without accepting lossy numeric input."""
    if isinstance(value, bool):
        raise _invalid_public_id()
    if isinstance(value, int):
        _validate_range(value)
        if value > MAX_JAVASCRIPT_SAFE_INTEGER:
            raise _public_id_string_required()
        return value
    if isinstance(value, str):
        if _PUBLIC_ID_RE.fullmatch(value) is None:
            raise _invalid_public_id()
        return _validate_range(int(value))
    raise _invalid_public_id()


def validate_public_id(value: int) -> int:
    """Validate an exact Python-domain public ID used in an API response."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_public_id()
    return _validate_range(value)


def serialize_public_id(value: int) -> str:
    return str(validate_public_id(value))


def serialize_public_ids_in_data(value: Any, *, key: str | None = None) -> Any:
    """Stringify integer identifiers inside intentionally untyped API evidence."""
    if isinstance(value, dict):
        return {
            item_key: serialize_public_ids_in_data(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [
            serialize_public_ids_in_data(
                item,
                key=key[:-1] if key is not None and key.endswith("s") else key,
            )
            for item in value
        ]
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and key is not None
        and (
            key.endswith("_id")
            or key in {"id", "record_id", "reference_id"}
        )
    ):
        return serialize_public_id(value)
    return value


PublicId = Annotated[
    int,
    AfterValidator(validate_public_id),
    PlainSerializer(serialize_public_id, return_type=str),
    WithJsonSchema(_PUBLIC_ID_JSON_SCHEMA),
]

PublicIdInput = Annotated[
    int,
    BeforeValidator(parse_public_id),
    WithJsonSchema(_PUBLIC_ID_JSON_SCHEMA),
]

PublicIdData = Annotated[
    dict[str, Any],
    PlainSerializer(serialize_public_ids_in_data, return_type=dict[str, Any]),
]
