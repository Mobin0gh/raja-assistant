"""In-memory reservation storage with an atomic per-record save.

The contract a store must satisfy is small but strict:

``save`` is atomic per reservation
    Either the complete new record replaces the stored one, or the call raises
    and the previously stored record is unchanged in *every* field. No partial
    record is ever observable through ``get`` or ``open_reservations``, and a
    failed save has no other side effect.

``save`` refuses to rewrite another intent
    A record may only replace the record with the same ``reservation_id``; if the
    stored record belongs to a different intent the write is refused rather than
    silently destroying it.

Everything here lives in process memory. It does not survive termination, and
saving ``SUBMITTING`` before a provider call is an *in-process ordering*
guarantee, not durable crash recovery. There is no concurrency control: atomic
per-call saves are not a transaction over read-modify-write, and this phase
assumes one command at a time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from raja_assistant.application.errors import RecordInconsistent
from raja_assistant.application.models import ReservationRecord


@runtime_checkable
class ReservationStore(Protocol):
    """The persistence seam the orchestrator depends on."""

    def get(self, reservation_id: str) -> ReservationRecord | None:
        """Return the stored record, or None when no such reservation exists."""

        ...

    def save(self, record: ReservationRecord) -> None:
        """Atomically store ``record``; a failed call leaves the prior record intact."""

        ...

    def open_reservations(self) -> tuple[ReservationRecord, ...]:
        """Return every non-terminal record, ordered by reservation id."""

        ...

    def open_for_intent(self, intent_id: str) -> tuple[ReservationRecord, ...]:
        """Return every non-terminal record for ``intent_id``, ordered by id."""

        ...


class InMemoryReservationStore:
    """A dict-backed store; the only store this phase ships."""

    def __init__(self) -> None:
        self._records: dict[str, ReservationRecord] = {}

    def get(self, reservation_id: str) -> ReservationRecord | None:
        return self._records.get(reservation_id)

    def save(self, record: ReservationRecord) -> None:
        reservation_id = record.reservation.reservation_id
        existing = self._records.get(reservation_id)
        if existing is not None and existing.intent.intent_id != record.intent.intent_id:
            raise RecordInconsistent(
                "intent_id",
                "an existing reservation id may not be reassigned to another intent",
            )
        # Validation above happens before any mutation, and the single dict
        # assignment below is atomic: a failure cannot leave a partial record.
        self._records[reservation_id] = record

    def open_reservations(self) -> tuple[ReservationRecord, ...]:
        return tuple(
            self._records[key]
            for key in sorted(self._records)
            if self._records[key].is_open
        )

    def open_for_intent(self, intent_id: str) -> tuple[ReservationRecord, ...]:
        return tuple(
            record
            for record in self.open_reservations()
            if record.intent.intent_id == intent_id
        )

    def __len__(self) -> int:
        return len(self._records)


@runtime_checkable
class IdFactory(Protocol):
    """Deterministic identity source; never a random module."""

    def next_reservation_id(self) -> str:
        ...

    def next_intent_id(self) -> str:
        ...


class SequentialIdFactory:
    """Count from one, so the same call sequence produces the same identifiers."""

    def __init__(self) -> None:
        self._reservations = 0
        self._intents = 0

    def next_reservation_id(self) -> str:
        self._reservations += 1
        return f"rsv-{self._reservations:04d}"

    def next_intent_id(self) -> str:
        self._intents += 1
        return f"int-{self._intents:04d}"


__all__ = [
    "IdFactory",
    "InMemoryReservationStore",
    "ReservationStore",
    "SequentialIdFactory",
]
