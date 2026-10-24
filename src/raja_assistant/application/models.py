"""Records and views the orchestration layer keeps about a reservation.

A :class:`ReservationRecord` is what is stored. It pairs the domain aggregate
with the intent it belongs to, and adds the few facts the domain deliberately
does not model:

``last_outcome``
    The provider outcome that produced the current state, or None when no answer
    was obtained. This is what keeps an ambiguous state diagnosable: a
    ``SUBMISSION_UNKNOWN`` reservation whose last outcome is ``PENDING`` means the
    provider has the request and has not decided; one whose last outcome is None
    means the acknowledgement itself was lost.

``last_detail``
    A short, non-identifying diagnostic string: an exception *class name*, a
    fixed phrase, or the provider's own ``detail`` for an ambiguous answer. Never
    a traceback, never a URL, never passenger data.

``conflict_reference``
    The audit mirror of a conflict latch. The authoritative latch lives in the
    conflict ledger; this field is what a future durable store could preserve.

``observed_hold_expires_at``
    The expiry a *lookup* reported for a hold. It is evidence, not a domain fact:
    it never becomes ``Reservation.hold_expires_at``, because a hold reported by
    a lookup is a diagnosis, not a booking.

A :class:`ReservationView` is what callers read: the stored record plus the two
process-local overlays (ledger latch, quarantine mark), so no reader ever has to
reconcile two sources of truth by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from raja_assistant.application.ports.models import ProviderOutcome
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.reservation.idempotency import derive_idempotency_key
from raja_assistant.domain.reservation.states import ReservationState
from raja_assistant.domain.validation import require, require_aware, require_optional_text


@dataclass(frozen=True, slots=True)
class ReservationRecord:
    """One reservation together with everything needed to interpret it."""

    reservation: Reservation
    intent: ReservationIntent
    last_outcome: ProviderOutcome | None = None
    last_detail: str | None = None
    conflict_reference: str | None = None
    observed_hold_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        require(
            self.reservation.intent_id == self.intent.intent_id,
            "intent",
            "must be the intent the reservation was opened for",
        )
        if self.last_outcome is not None:
            require(
                isinstance(self.last_outcome, ProviderOutcome),
                "last_outcome",
                "must be a provider outcome",
            )
        require_optional_text(self.conflict_reference, "conflict_reference", max_length=120)
        require_optional_text(self.last_detail, "last_detail", max_length=200)
        if self.observed_hold_expires_at is not None:
            require_aware(self.observed_hold_expires_at, "observed_hold_expires_at")

    @property
    def reservation_id(self) -> str:
        return self.reservation.reservation_id

    @property
    def state(self) -> ReservationState:
        return self.reservation.state

    @property
    def is_open(self) -> bool:
        """Return True while the reservation may still be acted on."""

        return not self.reservation.is_terminal

    @property
    def party_size(self) -> int:
        return self.intent.preferences.party_size

    @property
    def total_fare_irr(self) -> int:
        """Return the total fare for the whole party.

        ``ReservationIntent.fare_irr`` is the **per-passenger** fare of the
        quoted service, so the party total is derived here and never stored.
        """

        return self.intent.fare_irr * self.party_size

    @property
    def idempotency_key(self) -> str:
        """Derive the key for this reservation's current attempt epoch.

        Deterministic and never stored: a replayed submission of the same
        attempt reproduces the same key by construction.
        """

        return derive_idempotency_key(
            intent_id=self.intent.intent_id,
            service_id=self.intent.service_id,
            travel_date=self.intent.preferences.travel_date,
            passenger_refs=self.intent.passenger_refs,
            quota_class=self.intent.quota_class,
            attempt_epoch=self.reservation.attempt_epoch,
        )


@dataclass(frozen=True, slots=True)
class ReservationView:
    """A stored record plus the authoritative process-local overlays."""

    record: ReservationRecord
    conflict_reference: str | None = None
    quarantine_reason: str | None = None

    @property
    def reservation(self) -> Reservation:
        return self.record.reservation

    @property
    def intent(self) -> ReservationIntent:
        return self.record.intent

    @property
    def state(self) -> ReservationState:
        return self.record.state

    @property
    def total_fare_irr(self) -> int:
        return self.record.total_fare_irr

    @property
    def is_latched(self) -> bool:
        """Return True when a conflict latch blocks every mutating command."""

        return self.conflict_reference is not None

    @property
    def is_quarantined(self) -> bool:
        """Return True when a conflict was seen but could not be recorded."""

        return self.quarantine_reason is not None


__all__ = ["ReservationRecord", "ReservationView"]
