"""Typed errors raised by the orchestration layer.

These are *not* booking outcomes. A rejection, a hold, a pending answer, an
unavailable provider, and an ambiguous submission are results (see
:mod:`raja_assistant.application.ports.models`); the errors below mean the
orchestrator could not do its job safely: a command was aimed at a missing
reservation, a conflict must block every further action, a persistence step
failed, or a reconciliation call raised instead of answering.

Every type here is disjoint on purpose. In particular
:class:`ConflictLatchFailed` is deliberately **not** a subclass of
:class:`DuplicateSubmissionBlocked`: an error that says "the reservation is
blocked" must never be raised for a latch that does not exist.
"""

from __future__ import annotations


class OrchestrationError(Exception):
    """Base class for every failure raised by a use-case service."""


class UnknownReservation(OrchestrationError):
    """No reservation with the requested identifier is stored."""

    def __init__(self, reservation_id: str) -> None:
        self.reservation_id = reservation_id
        super().__init__(f"no reservation {reservation_id!r} is stored")


class RecordInconsistent(OrchestrationError):
    """A stored record disagrees with itself or with the value it was asked for."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class DuplicateSubmissionBlocked(OrchestrationError):
    """A duplicate submission was observed and the reservation is latched.

    Raised for a reservation whose conflict *is* recorded, either in the ledger
    or in its audit mirror. Every mutating command refuses while the latch is in
    force; there is no resolution flow in this phase.
    """

    def __init__(self, reservation_id: str, conflict_reference: str, command: str = "") -> None:
        self.reservation_id = reservation_id
        self.conflict_reference = conflict_reference
        self.command = command
        suffix = f" while running {command!r}" if command else ""
        super().__init__(
            f"reservation {reservation_id!r} is latched to existing provider reservation "
            f"{conflict_reference!r}{suffix}"
        )


class ConflictLatchFailed(OrchestrationError):
    """A duplicate submission was observed but recording it did not fully succeed.

    ``latched`` states exactly what happened: ``False`` means the authoritative
    ledger refused the write and the reservation is blocked only by the
    process-local quarantine; ``True`` means the ledger holds the latch but the
    audit mirror could not be saved.
    """

    def __init__(
        self,
        reservation_id: str,
        *,
        latched: bool,
        conflict_reference: str | None = None,
    ) -> None:
        self.reservation_id = reservation_id
        self.latched = latched
        self.conflict_reference = conflict_reference
        state = "recorded in the ledger but not mirrored" if latched else "not recorded"
        super().__init__(
            f"duplicate submission for {reservation_id!r} could not be stored; "
            f"the conflict is {state}"
        )


class ReservationQuarantined(OrchestrationError):
    """A conflict was observed and could not be recorded, so work is quarantined.

    Carries no provider reference: nothing about the conflict was persisted, and
    the error must not be read as evidence that a latch exists.
    """

    def __init__(self, reservation_id: str, reason: str, command: str = "") -> None:
        self.reservation_id = reservation_id
        self.reason = reason
        self.command = command
        suffix = f" while running {command!r}" if command else ""
        super().__init__(f"reservation {reservation_id!r} is quarantined: {reason}{suffix}")


class LookupFailed(OrchestrationError):
    """The provider raised during a reconciliation lookup.

    The stored reservation is left exactly as it was; the original exception is
    available as ``__cause__``. This is never evidence about the booking.
    """

    def __init__(self, reservation_id: str, state: object) -> None:
        self.reservation_id = reservation_id
        self.state = state
        state_value = getattr(state, "value", state)
        super().__init__(
            f"reconciliation lookup for {reservation_id!r} failed; the reservation remains "
            f"{state_value!r}"
        )


class StorageError(OrchestrationError):
    """A store write failed; the previously stored record is unchanged.

    Raised after a failed acknowledgement write too, where the reservation is
    left in ``SUBMITTING`` and must be reconciled rather than re-submitted.
    """

    def __init__(self, reservation_id: str, operation: str) -> None:
        self.reservation_id = reservation_id
        self.operation = operation
        super().__init__(f"could not {operation} reservation {reservation_id!r}")


__all__ = [
    "ConflictLatchFailed",
    "DuplicateSubmissionBlocked",
    "LookupFailed",
    "OrchestrationError",
    "RecordInconsistent",
    "ReservationQuarantined",
    "StorageError",
    "UnknownReservation",
]
