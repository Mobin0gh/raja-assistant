"""Reservation lifecycle states.

The state machine is the safety core of the product: a booking is irreversible
once the provider has it, so every state below exists to keep the engine from
claiming more than it knows.
"""

from __future__ import annotations

from enum import StrEnum


class ReservationState(StrEnum):
    """Explicit states a reservation can occupy."""

    DRAFT = "draft"
    VALIDATED = "validated"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTING = "submitting"
    SUBMISSION_UNKNOWN = "submission_unknown"
    HELD = "held"
    PAYMENT_PENDING = "payment_pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[ReservationState] = frozenset(
    {
        ReservationState.CONFIRMED,
        ReservationState.REJECTED,
        ReservationState.EXPIRED,
        ReservationState.CANCELLED,
    }
)

#: States in which the provider may already hold or have applied something.
IN_FLIGHT_STATES: frozenset[ReservationState] = frozenset(
    {
        ReservationState.SUBMITTING,
        ReservationState.SUBMISSION_UNKNOWN,
    }
)

#: States that exist before any request has been sent.
PRE_SUBMISSION_STATES: frozenset[ReservationState] = frozenset(
    {
        ReservationState.DRAFT,
        ReservationState.VALIDATED,
        ReservationState.AWAITING_APPROVAL,
    }
)


def is_terminal(state: ReservationState) -> bool:
    """Return True when no event may leave ``state``."""

    return state in TERMINAL_STATES
