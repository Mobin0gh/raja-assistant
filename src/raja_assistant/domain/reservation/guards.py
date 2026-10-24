"""Guards: the conditions that must hold before a transition may proceed.

Guards are pure functions of a reservation, its context, and the transition
payload. They never sleep, retry, or touch the network; they only refuse.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from raja_assistant.domain.errors import GuardReason, GuardViolation
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.reservation.events import TransitionPayload
from raja_assistant.domain.validation import require, require_aware

#: A human approval older than this is considered stale and must be repeated.
#: Ten minutes keeps the confirmed quote and the approved price close together.
APPROVAL_MAX_AGE = timedelta(minutes=10)

@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What an adapter claims to support, and whether it is authorized at all.

    Every capability defaults to False so that a default-constructed context can
    never submit, hold, or pay. A provider earns each capability explicitly.
    """

    authorized: bool = False
    book: bool = False
    hold: bool = False
    pay: bool = False
    lookup_by_reference: bool = False


@dataclass(frozen=True, slots=True)
class Approval:
    """A recorded human decision to submit one intent exactly once."""

    intent_id: str
    approved_by: str
    approved_at: datetime

    def __post_init__(self) -> None:
        require(bool(self.intent_id.strip()), "approval.intent_id", "must not be blank")
        require(bool(self.approved_by.strip()), "approval.approved_by", "must not be blank")
        require_aware(self.approved_at, "approval.approved_at")


@dataclass(frozen=True, slots=True)
class ReservationContext:
    """Everything a guard may consult that is not part of the reservation.

    ``open_reservations_for_intent`` counts *other* non-terminal reservations
    for the same intent; the caller queries it from persistence and passes the
    number in, keeping the domain free of database access.
    """

    now: datetime
    intent: ReservationIntent | None = None
    approval: Approval | None = None
    capabilities: ProviderCapabilities = ProviderCapabilities()
    open_reservations_for_intent: int = 0

    def __post_init__(self) -> None:
        require_aware(self.now, "context.now")
        require(
            self.open_reservations_for_intent >= 0,
            "open_reservations_for_intent",
            "must not be negative",
        )


Guard = Callable[[Reservation, ReservationContext, TransitionPayload], None]


def _intent(context: ReservationContext) -> ReservationIntent:
    if context.intent is None:
        raise GuardViolation(GuardReason.INTENT_MISSING)
    return context.intent


def _require_reason(payload: TransitionPayload) -> str:
    reason = payload.rejection_reason
    if reason is None or not reason.strip():
        raise GuardViolation(GuardReason.REJECTION_REASON_MISSING)
    return reason


def _require_reference(payload: TransitionPayload) -> str:
    reference = payload.provider_reference
    if reference is None or not reference.strip():
        raise GuardViolation(GuardReason.PROVIDER_REFERENCE_MISSING)
    return reference


def _require_evidence(payload: TransitionPayload) -> str:
    note = payload.evidence_note
    if note is None or not note.strip():
        raise GuardViolation(GuardReason.EVIDENCE_MISSING)
    return note


def guard_validate(reservation: Reservation, context: ReservationContext,
                   payload: TransitionPayload) -> None:
    """Local validation only; there is nothing to prove except intent presence."""

    _intent(context)


def guard_rejection_reason(reservation: Reservation, context: ReservationContext,
                           payload: TransitionPayload) -> None:
    _require_reason(payload)


def guard_quote_not_expired(reservation: Reservation, context: ReservationContext,
                            payload: TransitionPayload) -> None:
    intent = _intent(context)
    if context.now >= intent.quote_expires_at:
        raise GuardViolation(GuardReason.QUOTE_EXPIRED)


def guard_quote_expired(reservation: Reservation, context: ReservationContext,
                        payload: TransitionPayload) -> None:
    intent = _intent(context)
    if context.now < intent.quote_expires_at:
        raise GuardViolation(GuardReason.QUOTE_NOT_EXPIRED)


def guard_submit(reservation: Reservation, context: ReservationContext,
                 payload: TransitionPayload) -> None:
    """The single most important guard in the system.

    Submission requires an authorized provider that actually supports booking,
    a recorded human approval for *this* intent, a live quote, and no other
    outstanding reservation for the same intent.
    """

    if not (context.capabilities.authorized and context.capabilities.book):
        raise GuardViolation(GuardReason.PROVIDER_NOT_AUTHORIZED)
    approval = context.approval
    if approval is None:
        raise GuardViolation(GuardReason.APPROVAL_REQUIRED)
    if approval.intent_id != reservation.intent_id:
        raise GuardViolation(GuardReason.APPROVAL_INTENT_MISMATCH)
    if approval.approved_at > context.now or context.now - approval.approved_at > APPROVAL_MAX_AGE:
        raise GuardViolation(GuardReason.APPROVAL_STALE)
    intent = _intent(context)
    if context.now >= intent.quote_expires_at:
        raise GuardViolation(GuardReason.QUOTE_EXPIRED)
    if context.open_reservations_for_intent > 0:
        raise GuardViolation(GuardReason.DUPLICATE_SUBMISSION_PENDING)


def guard_confirmed(reservation: Reservation, context: ReservationContext,
                    payload: TransitionPayload) -> None:
    _require_reference(payload)


def guard_held(reservation: Reservation, context: ReservationContext,
               payload: TransitionPayload) -> None:
    if not context.capabilities.hold:
        raise GuardViolation(GuardReason.CAPABILITY_UNSUPPORTED, "provider has no hold support")
    hold_expires_at = payload.hold_expires_at
    if (
        hold_expires_at is None
        or hold_expires_at.tzinfo is None
        or hold_expires_at.utcoffset() is None
        or hold_expires_at <= context.now
    ):
        raise GuardViolation(GuardReason.HOLD_EXPIRY_INVALID)


def guard_hold_expired(reservation: Reservation, context: ReservationContext,
                       payload: TransitionPayload) -> None:
    hold_expires_at = reservation.hold_expires_at
    if hold_expires_at is None or context.now < hold_expires_at:
        raise GuardViolation(GuardReason.HOLD_NOT_EXPIRED)


def guard_pay(reservation: Reservation, context: ReservationContext,
              payload: TransitionPayload) -> None:
    if not context.capabilities.pay:
        raise GuardViolation(GuardReason.CAPABILITY_UNSUPPORTED, "provider has no payment support")


def guard_reconciled_as_booked(reservation: Reservation, context: ReservationContext,
                               payload: TransitionPayload) -> None:
    _require_reference(payload)
    _require_evidence(payload)


def guard_reconciled_as_not_booked(reservation: Reservation, context: ReservationContext,
                                   payload: TransitionPayload) -> None:
    _require_evidence(payload)


def guard_always(reservation: Reservation, context: ReservationContext,
                 payload: TransitionPayload) -> None:
    """Explicit no-op guard, used where the state itself is the whole condition."""


__all__ = [
    "APPROVAL_MAX_AGE",
    "Approval",
    "Guard",
    "ProviderCapabilities",
    "ReservationContext",
    "guard_always",
    "guard_confirmed",
    "guard_held",
    "guard_hold_expired",
    "guard_pay",
    "guard_quote_expired",
    "guard_quote_not_expired",
    "guard_reconciled_as_booked",
    "guard_reconciled_as_not_booked",
    "guard_rejection_reason",
    "guard_submit",
    "guard_validate",
]
