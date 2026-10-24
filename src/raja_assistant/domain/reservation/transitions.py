"""The explicit reservation transition table.

Anything not listed in :data:`TRANSITIONS` is illegal, by construction. The
table is the single source of truth: docs, tests, and the future persistence
layer all read it rather than duplicating the rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from raja_assistant.domain.errors import GuardReason, GuardViolation, IllegalTransition
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.reservation import guards
from raja_assistant.domain.reservation.events import ReservationEvent, TransitionPayload
from raja_assistant.domain.reservation.states import ReservationState

Changes = dict[str, object]
Effect = Callable[[Reservation, guards.ReservationContext, TransitionPayload], Changes]


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    """One legal edge in the machine."""

    source: ReservationState
    event: ReservationEvent
    target: ReservationState
    guard: guards.Guard
    effect: Effect


def _no_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {}


def _rejection_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {"rejection_reason": payload.rejection_reason}


def _confirmation_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {"provider_reference": payload.provider_reference}


def _hold_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {"hold_expires_at": payload.hold_expires_at}


def _reconciled_booked_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {
        "provider_reference": payload.provider_reference,
        "evidence_note": payload.evidence_note,
    }


def _reconciled_not_booked_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    return {
        "rejection_reason": payload.rejection_reason or "reconciled_not_booked",
        "evidence_note": payload.evidence_note,
    }


def _reconciled_inconclusive_effect(
    reservation: Reservation,
    context: guards.ReservationContext,
    payload: TransitionPayload,
) -> Changes:
    if payload.evidence_note is None:
        return {}
    return {"evidence_note": payload.evidence_note}


def _spec(
    source: ReservationState,
    event: ReservationEvent,
    target: ReservationState,
    guard: guards.Guard,
    effect: Effect = _no_effect,
) -> TransitionSpec:
    return TransitionSpec(source=source, event=event, target=target, guard=guard, effect=effect)


_TRANSITION_LIST: tuple[TransitionSpec, ...] = (
    # Pre-submission
    _spec(ReservationState.DRAFT, ReservationEvent.VALIDATE,
          ReservationState.VALIDATED, guards.guard_validate),
    _spec(ReservationState.DRAFT, ReservationEvent.VALIDATION_FAILED,
          ReservationState.REJECTED, guards.guard_rejection_reason, _rejection_effect),
    _spec(ReservationState.VALIDATED, ReservationEvent.REQUEST_APPROVAL,
          ReservationState.AWAITING_APPROVAL, guards.guard_quote_not_expired),
    _spec(ReservationState.VALIDATED, ReservationEvent.QUOTE_EXPIRED,
          ReservationState.EXPIRED, guards.guard_quote_expired),
    _spec(ReservationState.AWAITING_APPROVAL, ReservationEvent.APPROVE_AND_SUBMIT,
          ReservationState.SUBMITTING, guards.guard_submit),
    _spec(ReservationState.AWAITING_APPROVAL, ReservationEvent.DECLINE,
          ReservationState.CANCELLED, guards.guard_always),
    _spec(ReservationState.AWAITING_APPROVAL, ReservationEvent.QUOTE_EXPIRED,
          ReservationState.EXPIRED, guards.guard_quote_expired),
    # In flight
    _spec(ReservationState.SUBMITTING, ReservationEvent.ACK_CONFIRMED,
          ReservationState.CONFIRMED, guards.guard_confirmed, _confirmation_effect),
    _spec(ReservationState.SUBMITTING, ReservationEvent.ACK_HELD,
          ReservationState.HELD, guards.guard_held, _hold_effect),
    _spec(ReservationState.SUBMITTING, ReservationEvent.ACK_REJECTED,
          ReservationState.REJECTED, guards.guard_rejection_reason, _rejection_effect),
    _spec(ReservationState.SUBMITTING, ReservationEvent.ACK_UNKNOWN,
          ReservationState.SUBMISSION_UNKNOWN, guards.guard_always),
    _spec(ReservationState.SUBMITTING, ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT,
          ReservationState.AWAITING_APPROVAL, guards.guard_always),
    _spec(ReservationState.SUBMITTING, ReservationEvent.AUTH_EXPIRED,
          ReservationState.AWAITING_APPROVAL, guards.guard_always),
    _spec(ReservationState.SUBMITTING, ReservationEvent.RATE_LIMITED,
          ReservationState.AWAITING_APPROVAL, guards.guard_always),
    # Hold and payment (modelled only; no integration exists)
    _spec(ReservationState.HELD, ReservationEvent.PAY,
          ReservationState.PAYMENT_PENDING, guards.guard_pay),
    _spec(ReservationState.HELD, ReservationEvent.HOLD_EXPIRED,
          ReservationState.EXPIRED, guards.guard_hold_expired),
    _spec(ReservationState.PAYMENT_PENDING, ReservationEvent.PAYMENT_CONFIRMED,
          ReservationState.CONFIRMED, guards.guard_confirmed, _confirmation_effect),
    _spec(ReservationState.PAYMENT_PENDING, ReservationEvent.PAYMENT_FAILED,
          ReservationState.REJECTED, guards.guard_rejection_reason, _rejection_effect),
    _spec(ReservationState.PAYMENT_PENDING, ReservationEvent.PAYMENT_AMBIGUOUS,
          ReservationState.SUBMISSION_UNKNOWN, guards.guard_always),
    # Reconciliation of an ambiguous outcome
    _spec(ReservationState.SUBMISSION_UNKNOWN, ReservationEvent.RECONCILE_BOOKED,
          ReservationState.CONFIRMED, guards.guard_reconciled_as_booked,
          _reconciled_booked_effect),
    _spec(ReservationState.SUBMISSION_UNKNOWN, ReservationEvent.RECONCILE_NOT_BOOKED,
          ReservationState.REJECTED, guards.guard_reconciled_as_not_booked,
          _reconciled_not_booked_effect),
    _spec(ReservationState.SUBMISSION_UNKNOWN, ReservationEvent.RECONCILE_INCONCLUSIVE,
          ReservationState.SUBMISSION_UNKNOWN, guards.guard_always,
          _reconciled_inconclusive_effect),
)

TRANSITIONS: Mapping[tuple[ReservationState, ReservationEvent], TransitionSpec] = {
    (spec.source, spec.event): spec for spec in _TRANSITION_LIST
}


def transition(
    reservation: Reservation,
    event: ReservationEvent,
    context: guards.ReservationContext,
    payload: TransitionPayload | None = None,
) -> Reservation:
    """Apply ``event`` and return the resulting immutable reservation.

    Raises :class:`IllegalTransition` when the edge does not exist and
    :class:`GuardViolation` when its guard refuses.
    """

    payload = payload if payload is not None else TransitionPayload()
    spec = TRANSITIONS.get((reservation.state, event))
    if spec is None:
        raise IllegalTransition(reservation.state, event)
    if context.now < reservation.updated_at:
        raise GuardViolation(GuardReason.CLOCK_BACKWARDS)
    spec.guard(reservation, context, payload)
    changes = spec.effect(reservation, context, payload)
    return replace(reservation, state=spec.target, updated_at=context.now, **changes)


def allowed_events(state: ReservationState) -> tuple[ReservationEvent, ...]:
    """Return every event accepted in ``state``, in a stable order."""

    return tuple(
        sorted(
            (event for (source, event) in TRANSITIONS if source == state),
            key=lambda event: event.value,
        )
    )


def is_allowed(state: ReservationState, event: ReservationEvent) -> bool:
    """Return True when ``event`` has an edge out of ``state``."""

    return (state, event) in TRANSITIONS
