"""Reservation intent and the immutable reservation aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences
from raja_assistant.domain.models.service import ServiceId
from raja_assistant.domain.reservation.states import (
    PRE_SUBMISSION_STATES,
    ReservationState,
    is_terminal,
)
from raja_assistant.domain.validation import (
    require,
    require_aware,
    require_optional_text,
    require_token,
)


@dataclass(frozen=True, slots=True)
class ReservationIntent:
    """Everything a traveller has decided, frozen at quote time.

    An intent is the unit of human approval and of idempotency: a reservation
    can only ever belong to one intent, and a new booking attempt after a
    reconciled failure is a *new* intent rather than a mutated one.
    """

    intent_id: str
    preferences: TripPreferences
    service_id: ServiceId
    quota_class: QuotaClass
    passenger_refs: tuple[PassengerRef, ...]
    fare_irr: int
    quote_expires_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        require_token(self.intent_id, "intent_id")
        require_aware(self.created_at, "created_at")
        require_aware(self.quote_expires_at, "quote_expires_at")
        require(
            self.quote_expires_at > self.created_at,
            "quote_expires_at",
            "must be after created_at",
        )
        require(bool(self.passenger_refs), "passenger_refs", "must not be empty")
        require(
            len(self.passenger_refs) == self.preferences.party_size,
            "passenger_refs",
            "must match the party size",
        )
        tokens = [ref.token for ref in self.passenger_refs]
        require(len(set(tokens)) == len(tokens), "passenger_refs", "must be unique")
        require(self.fare_irr > 0, "fare_irr", "must be positive")
        if self.preferences.max_fare_irr is not None:
            require(
                self.fare_irr <= self.preferences.max_fare_irr,
                "fare_irr",
                "must not exceed the traveller's maximum fare",
            )
        require(
            self.quota_class in self.preferences.quota_classes
            or not self.preferences.quota_classes,
            "quota_class",
            "must be one of the requested quota classes",
        )


@dataclass(frozen=True, slots=True)
class Reservation:
    """Immutable reservation aggregate.

    All changes go through the state machine, which returns a new instance.
    Construction is still validated so that no code path can fabricate a
    ``CONFIRMED`` reservation without a provider reference.
    """

    reservation_id: str
    intent_id: str
    state: ReservationState
    attempt_epoch: int
    created_at: datetime
    updated_at: datetime
    provider_reference: str | None = None
    rejection_reason: str | None = None
    hold_expires_at: datetime | None = None
    evidence_note: str | None = None

    def __post_init__(self) -> None:
        require_token(self.reservation_id, "reservation_id")
        require_token(self.intent_id, "intent_id")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        require(self.updated_at >= self.created_at, "updated_at", "must not precede created_at")
        require(self.attempt_epoch >= 1, "attempt_epoch", "must be at least 1")
        require_optional_text(self.provider_reference, "provider_reference", max_length=120)
        require_optional_text(self.rejection_reason, "rejection_reason", max_length=200)
        require_optional_text(self.evidence_note, "evidence_note", max_length=500)

        if self.state is ReservationState.CONFIRMED:
            require(
                self.provider_reference is not None,
                "provider_reference",
                "is required before a reservation may be confirmed",
            )
        if self.state is ReservationState.REJECTED:
            require(
                self.rejection_reason is not None,
                "rejection_reason",
                "is required before a reservation may be rejected",
            )
        if self.state is ReservationState.HELD:
            require(
                self.hold_expires_at is not None,
                "hold_expires_at",
                "is required while a seat is held",
            )
        if self.state in PRE_SUBMISSION_STATES:
            require(
                self.provider_reference is None,
                "provider_reference",
                "must be empty before submission",
            )
            require(
                self.hold_expires_at is None,
                "hold_expires_at",
                "must be empty before submission",
            )
            require(self.evidence_note is None, "evidence_note", "must be empty before submission")

    @classmethod
    def start(cls, *, reservation_id: str, intent_id: str, now: datetime) -> Reservation:
        """Create a reservation in ``DRAFT`` with the first attempt epoch."""

        return cls(
            reservation_id=reservation_id,
            intent_id=intent_id,
            state=ReservationState.DRAFT,
            attempt_epoch=1,
            created_at=now,
            updated_at=now,
        )

    @property
    def is_terminal(self) -> bool:
        """Return True when no further transition is possible."""

        return is_terminal(self.state)
