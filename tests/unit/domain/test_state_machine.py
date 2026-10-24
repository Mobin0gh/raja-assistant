"""The transition table, exhaustively.

Two complementary tests: every declared edge actually works, and every
(state, event) pair that is not declared is refused.
"""

from __future__ import annotations

import pytest

from raja_assistant.domain.errors import IllegalTransition
from raja_assistant.domain.reservation.events import ReservationEvent
from raja_assistant.domain.reservation.states import TERMINAL_STATES, ReservationState
from raja_assistant.domain.reservation.transitions import (
    TRANSITIONS,
    allowed_events,
    is_allowed,
    transition,
)
from support import build_in_state, context_for, payload_for

ALL_STATES = tuple(ReservationState)
ALL_EVENTS = tuple(ReservationEvent)

EXPECTED_ALLOWED: dict[ReservationState, tuple[ReservationEvent, ...]] = {
    ReservationState.DRAFT: (
        ReservationEvent.VALIDATE,
        ReservationEvent.VALIDATION_FAILED,
    ),
    ReservationState.VALIDATED: (
        ReservationEvent.QUOTE_EXPIRED,
        ReservationEvent.REQUEST_APPROVAL,
    ),
    ReservationState.AWAITING_APPROVAL: (
        ReservationEvent.APPROVE_AND_SUBMIT,
        ReservationEvent.DECLINE,
        ReservationEvent.QUOTE_EXPIRED,
    ),
    ReservationState.SUBMITTING: (
        ReservationEvent.ACK_CONFIRMED,
        ReservationEvent.ACK_HELD,
        ReservationEvent.ACK_REJECTED,
        ReservationEvent.ACK_UNKNOWN,
        ReservationEvent.AUTH_EXPIRED,
        ReservationEvent.RATE_LIMITED,
        ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT,
    ),
    ReservationState.SUBMISSION_UNKNOWN: (
        ReservationEvent.RECONCILE_BOOKED,
        ReservationEvent.RECONCILE_INCONCLUSIVE,
        ReservationEvent.RECONCILE_NOT_BOOKED,
    ),
    ReservationState.HELD: (
        ReservationEvent.HOLD_EXPIRED,
        ReservationEvent.PAY,
    ),
    ReservationState.PAYMENT_PENDING: (
        ReservationEvent.PAYMENT_AMBIGUOUS,
        ReservationEvent.PAYMENT_CONFIRMED,
        ReservationEvent.PAYMENT_FAILED,
    ),
    ReservationState.CONFIRMED: (),
    ReservationState.REJECTED: (),
    ReservationState.EXPIRED: (),
    ReservationState.CANCELLED: (),
}


def test_every_state_is_documented_in_the_expected_table() -> None:
    assert set(EXPECTED_ALLOWED) == set(ALL_STATES)


@pytest.mark.parametrize("state", ALL_STATES, ids=lambda state: state.value)
def test_allowed_events_match_the_declared_table(state: ReservationState) -> None:
    assert allowed_events(state) == tuple(
        sorted(EXPECTED_ALLOWED[state], key=lambda event: event.value)
    )


@pytest.mark.parametrize("state", ALL_STATES, ids=lambda state: state.value)
def test_terminal_states_have_no_outgoing_edges(state: ReservationState) -> None:
    if state not in TERMINAL_STATES:
        return
    assert allowed_events(state) == ()
    spec_sources = {source for (source, _event) in TRANSITIONS}
    assert state not in spec_sources


@pytest.mark.parametrize(
    ("state", "event"),
    sorted(TRANSITIONS, key=lambda pair: (pair[0].value, pair[1].value)),
    ids=lambda value: getattr(value, "value", str(value)),
)
def test_declared_transition_reaches_its_target(
    state: ReservationState, event: ReservationEvent
) -> None:
    reservation = build_in_state(state)
    context = context_for(state, event)

    updated = transition(reservation, event, context, payload_for(event))

    assert updated.state is TRANSITIONS[(state, event)].target
    assert updated.updated_at == context.now
    assert updated.created_at == reservation.created_at
    assert updated.reservation_id == reservation.reservation_id
    assert updated.intent_id == reservation.intent_id
    assert updated.attempt_epoch == reservation.attempt_epoch
    # The aggregate is immutable: the original object is untouched.
    assert reservation.state is state


def test_undeclared_pairs_raise_illegal_transition() -> None:
    checked = 0
    for state in ALL_STATES:
        for event in ALL_EVENTS:
            if is_allowed(state, event):
                continue
            reservation = build_in_state(state)
            with pytest.raises(IllegalTransition) as error:
                transition(reservation, event, context_for(state, event), payload_for(event))
            assert error.value.state is state
            assert error.value.event is event
            checked += 1
    assert checked == len(ALL_STATES) * len(ALL_EVENTS) - len(TRANSITIONS)


def test_send_failure_returns_to_awaiting_approval_without_confirmation() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    context = context_for(ReservationState.SUBMITTING, ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT)

    updated = transition(reservation, ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT, context)

    assert updated.state is ReservationState.AWAITING_APPROVAL
    assert updated.provider_reference is None


@pytest.mark.parametrize(
    "event",
    [
        ReservationEvent.AUTH_EXPIRED,
        ReservationEvent.RATE_LIMITED,
        ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT,
    ],
)
def test_recoverable_submission_failures_drop_out_of_flight(event: ReservationEvent) -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)

    updated = transition(reservation, event, context_for(ReservationState.SUBMITTING, event))

    assert updated.state is ReservationState.AWAITING_APPROVAL
    assert updated.state not in TERMINAL_STATES


def test_ambiguous_acknowledgement_freezes_instead_of_retrying() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    context = context_for(ReservationState.SUBMITTING, ReservationEvent.ACK_UNKNOWN)

    updated = transition(reservation, ReservationEvent.ACK_UNKNOWN, context)

    assert updated.state is ReservationState.SUBMISSION_UNKNOWN
    assert updated.provider_reference is None
    with pytest.raises(IllegalTransition):
        transition(updated, ReservationEvent.APPROVE_AND_SUBMIT, context)
    with pytest.raises(IllegalTransition):
        transition(updated, ReservationEvent.ACK_CONFIRMED, context)


def test_reconcile_not_booked_records_a_reason() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    context = context_for(
        ReservationState.SUBMISSION_UNKNOWN, ReservationEvent.RECONCILE_NOT_BOOKED
    )

    updated = transition(
        reservation,
        ReservationEvent.RECONCILE_NOT_BOOKED,
        context,
        payload_for(ReservationEvent.RECONCILE_NOT_BOOKED),
    )

    assert updated.state is ReservationState.REJECTED
    assert updated.rejection_reason == "reconciled_not_booked"
    assert updated.evidence_note


def test_inconclusive_reconciliation_stays_unknown() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    context = context_for(
        ReservationState.SUBMISSION_UNKNOWN, ReservationEvent.RECONCILE_INCONCLUSIVE
    )

    updated = transition(
        reservation,
        ReservationEvent.RECONCILE_INCONCLUSIVE,
        context,
        payload_for(ReservationEvent.RECONCILE_INCONCLUSIVE),
    )

    assert updated.state is ReservationState.SUBMISSION_UNKNOWN
    assert updated.updated_at == context.now
