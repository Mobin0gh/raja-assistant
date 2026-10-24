"""Pseudonymous passenger references.

Phase P1 stores no passenger personal data. The domain only ever sees an opaque
local token; mapping a token to a real traveller belongs to the encrypted
identity adapter in a later phase, and never to the domain layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from raja_assistant.domain.validation import require

_PASSENGER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{4,64}")


@dataclass(frozen=True, slots=True, order=True)
class PassengerRef:
    """Opaque, non-identifying reference to a locally stored traveller."""

    token: str

    def __post_init__(self) -> None:
        require(isinstance(self.token, str), "passenger_token", "must be a string")
        require(
            bool(_PASSENGER_TOKEN_PATTERN.fullmatch(self.token)),
            "passenger_token",
            "must match [A-Za-z0-9_-]{4,64}",
        )
