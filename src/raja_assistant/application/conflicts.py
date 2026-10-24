"""Conflict latches and the fail-closed quarantine.

Two separate mechanisms, with two different meanings:

``ConflictLedger``
    The authoritative record of "this intent already has a live reservation
    under a different provider reference". It carries the reference, so a
    latched reservation can be explained.

``Quarantine``
    What is left when the latch could not be written at all. It carries **no**
    reference, only the fact that something went wrong, and it blocks every
    mutating command for the life of the process. It is deliberately weaker
    than a latch: it must never be presented as proof that a conflict exists.

Both live in process memory. Neither survives termination, and nothing here is
durable crash recovery.

Why the quarantine is not a ledger fallback: writing a mirror without a
reference would misrepresent a latch that was never established. Quarantine is
unmistakable, un-clearable, and cannot be confused with one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from raja_assistant.application.errors import RecordInconsistent

QUARANTINE_REASON = "duplicate conflict observed; the latch could not be recorded"


@runtime_checkable
class ConflictLedger(Protocol):
    """Authoritative, per-reservation record of an unrecordable conflict."""

    def latch(self, reservation_id: str, conflict_reference: str) -> None:
        """Record that ``reservation_id`` collides with a live provider reservation."""

        ...

    def reference_for(self, reservation_id: str) -> str | None:
        """Return the conflicting provider reference, or None when not latched."""

        ...


class InMemoryConflictLedger:
    """A volatile ledger; adequate for one desktop process and nothing more."""

    def __init__(self) -> None:
        self._references: dict[str, str] = {}

    def latch(self, reservation_id: str, conflict_reference: str) -> None:
        existing = self._references.get(reservation_id)
        if existing is None:
            self._references[reservation_id] = conflict_reference
            return
        if existing != conflict_reference:
            raise RecordInconsistent(
                "conflict_reference",
                "a different conflict is already recorded for this reservation",
            )

    def reference_for(self, reservation_id: str) -> str | None:
        return self._references.get(reservation_id)

    def __len__(self) -> int:
        return len(self._references)


class Quarantine:
    """Process-local marks for reservations whose conflict could not be latched."""

    def __init__(self) -> None:
        self._reasons: dict[str, str] = {}

    def mark(self, reservation_id: str, reason: str = QUARANTINE_REASON) -> None:
        """Block ``reservation_id`` for the rest of the process; first reason wins."""

        self._reasons.setdefault(reservation_id, reason)

    def reason_for(self, reservation_id: str) -> str | None:
        return self._reasons.get(reservation_id)

    def __len__(self) -> int:
        return len(self._reasons)


__all__ = [
    "QUARANTINE_REASON",
    "ConflictLedger",
    "InMemoryConflictLedger",
    "Quarantine",
]
