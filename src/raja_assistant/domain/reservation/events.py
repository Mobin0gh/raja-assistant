"""Events accepted by the reservation state machine and their payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReservationEvent(StrEnum):
    """Every event that can be applied to a reservation.

    Read events as facts observed by the engine, not as instructions: the
    provider acknowledging an outcome is an event, retrying is not.
    """

    VALIDATE = "validate"
    VALIDATION_FAILED = "validation_failed"
    REQUEST_APPROVAL = "request_approval"
    DECLINE = "decline"
    APPROVE_AND_SUBMIT = "approve_and_submit"
    QUOTE_EXPIRED = "quote_expired"
    ACK_CONFIRMED = "ack_confirmed"
    ACK_HELD = "ack_held"
    ACK_REJECTED = "ack_rejected"
    ACK_UNKNOWN = "ack_unknown"
    SEND_FAILED_NO_SIDE_EFFECT = "send_failed_no_side_effect"
    AUTH_EXPIRED = "auth_expired"
    RATE_LIMITED = "rate_limited"
    HOLD_EXPIRED = "hold_expired"
    PAY = "pay"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_AMBIGUOUS = "payment_ambiguous"
    RECONCILE_BOOKED = "reconcile_booked"
    RECONCILE_NOT_BOOKED = "reconcile_not_booked"
    RECONCILE_INCONCLUSIVE = "reconcile_inconclusive"


@dataclass(frozen=True, slots=True)
class TransitionPayload:
    """Evidence attached to a single transition.

    Fields are optional because most events carry no evidence. Guards decide
    which field is mandatory for a given event; the payload itself never grants
    permission to acknowledge an outcome.
    """

    provider_reference: str | None = None
    rejection_reason: str | None = None
    hold_expires_at: datetime | None = None
    evidence_note: str | None = None
