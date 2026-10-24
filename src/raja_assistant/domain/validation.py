"""Small, dependency-free validation helpers used by the domain models."""

from __future__ import annotations

import re
from datetime import datetime

from raja_assistant.domain.errors import ValidationError

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def require(condition: bool, field: str, message: str) -> None:
    """Raise :class:`ValidationError` unless ``condition`` holds."""

    if not condition:
        raise ValidationError(field, message)


def require_token(value: str, field: str) -> None:
    """Require an opaque identifier made of URL-safe ASCII characters."""

    require(isinstance(value, str), field, "must be a string")
    require(bool(_TOKEN_PATTERN.fullmatch(value)), field, "must match [A-Za-z0-9._:-]{1,64}")


def require_aware(value: datetime, field: str) -> None:
    """Require a timezone-aware datetime; naive timestamps are ambiguous."""

    require(value.tzinfo is not None and value.utcoffset() is not None, field,
            "must be timezone-aware")


def require_optional_text(value: str | None, field: str, max_length: int = 200) -> None:
    """Require a non-blank, bounded string when a value is present."""

    if value is None:
        return
    require(isinstance(value, str), field, "must be a string")
    require(bool(value.strip()), field, "must not be blank")
    require(len(value) <= max_length, field, f"must be at most {max_length} characters")
