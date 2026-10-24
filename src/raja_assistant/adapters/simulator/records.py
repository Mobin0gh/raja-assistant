"""The simulator's internal record of what it believes it booked.

A record is the simulator's ground truth. Note that it is *not* what a caller
always learns: an ambiguous submission creates a confirmed record while reporting
:class:`~raja_assistant.application.ports.models.SubmissionOutcomeUnknown`. Only a
lookup reveals the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from raja_assistant.domain.models.preferences import QuotaClass
from raja_assistant.domain.models.service import ServiceId
from raja_assistant.domain.validation import require, require_aware, require_token


class SimulatedReservationStatus(StrEnum):
    """What the simulator believes happened to one submission."""

    CONFIRMED = "confirmed"
    HELD = "held"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class SimulatedReservation:
    """One accepted submission, stored under its idempotency key."""

    idempotency_key: str
    intent_id: str
    service_id: ServiceId
    quota_class: QuotaClass
    party_size: int
    attempt_epoch: int
    status: SimulatedReservationStatus
    provider_reference: str
    accepted_at: datetime
    hold_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        require(bool(self.idempotency_key.strip()), "idempotency_key", "must not be blank")
        require_token(self.intent_id, "intent_id")
        require(self.party_size >= 1, "party_size", "must be positive")
        require(self.attempt_epoch >= 1, "attempt_epoch", "must be at least 1")
        require(bool(self.provider_reference.strip()), "provider_reference", "must not be blank")
        require_aware(self.accepted_at, "accepted_at")
        if self.status is SimulatedReservationStatus.HELD:
            require(
                self.hold_expires_at is not None,
                "hold_expires_at",
                "is required while the simulated reservation is held",
            )
        else:
            require(
                self.hold_expires_at is None,
                "hold_expires_at",
                "is only valid for a held simulated reservation",
            )


__all__ = ["SimulatedReservation", "SimulatedReservationStatus"]
