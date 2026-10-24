"""System-level invariants: the properties that make double booking impossible."""

from __future__ import annotations

import random

import pytest

from raja_assistant.domain.errors import DomainError, GuardReason, GuardViolation, IllegalTransition
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.reservation.events import (
    ReservationEvent,
    TransitionPayload,
)
from raja_assistant.domain.reservation.guards import (
    Approval,
    ProviderCapabilities,
    ReservationContext,
)
from raja_assistant.domain.reservation.states import (
    TERMINAL_STATES,
    ReservationState,
)
from raja_assistant.domain.reservation.transitions import transition
from support import (
    NOW,
    build_in_state,
    context_for,
    make_context,
    make_intent,
    payload_for,
)


def test_a_submission_cannot_be_approved_twice() -> None:
    reservation = build_in_state(ReservationState.AWAITING_APPROVAL)
    context = context_for(ReservationState.AWAITING_APPROVAL, ReservationEvent.APPROVE_AND_SUBMIT)

    submitted = transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)

    assert submitted.state is ReservationState.SUBMITTING
    with pytest.raises(IllegalTransition):
        transition(submitted, ReservationEvent.APPROVE_AND_SUBMIT, context)


def test_a_second_open_reservation_blocks_submission() -> None:
    reservation = build_in_state(ReservationState.AWAITING_APPROVAL)
    context = make_context(open_reservations_for_intent=1)

    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)

    assert error.value.reason is GuardReason.DUPLICATE_SUBMISSION_PENDING


def test_unknown_submission_cannot_be_resubmitted() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    context = make_context()

    for event in (
        ReservationEvent.APPROVE_AND_SUBMIT,
        ReservationEvent.ACK_CONFIRMED,
        ReservationEvent.ACK_HELD,
        ReservationEvent.VALIDATE,
        ReservationEvent.REQUEST_APPROVAL,
    ):
        with pytest.raises(IllegalTransition):
            transition(reservation, event, context, payload_for(event))


def test_payment_pending_cannot_be_resubmitted() -> None:
    reservation = build_in_state(ReservationState.PAYMENT_PENDING)
    with pytest.raises(IllegalTransition):
        transition(
            reservation,
            ReservationEvent.APPROVE_AND_SUBMIT,
            context_for(ReservationState.PAYMENT_PENDING, ReservationEvent.APPROVE_AND_SUBMIT),
        )


def test_confirmed_state_is_only_reachable_with_a_provider_reference() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    with pytest.raises(GuardViolation):
        transition(reservation, ReservationEvent.ACK_CONFIRMED, make_context())
    confirmed = transition(
        reservation,
        ReservationEvent.ACK_CONFIRMED,
        make_context(),
        TransitionPayload(provider_reference="PNR-1"),
    )
    assert confirmed.state is ReservationState.CONFIRMED
    assert confirmed.provider_reference == "PNR-1"


def test_terminal_states_refuse_every_event() -> None:
    for state in sorted(TERMINAL_STATES, key=lambda item: item.value):
        reservation = build_in_state(state)
        assert reservation.is_terminal
        for event in ReservationEvent:
            with pytest.raises(IllegalTransition):
                transition(reservation, event, make_context(), payload_for(event))


def test_no_payload_can_fake_a_confirmation_from_a_pre_submission_state() -> None:
    for state in (
        ReservationState.DRAFT,
        ReservationState.VALIDATED,
        ReservationState.AWAITING_APPROVAL,
    ):
        reservation = build_in_state(state)
        with pytest.raises(IllegalTransition):
            transition(
                reservation,
                ReservationEvent.ACK_CONFIRMED,
                make_context(),
                TransitionPayload(provider_reference="PNR-FORGED"),
            )


def _random_context(rng: random.Random, intent_id: str) -> ReservationContext:
    """Return one of several contexts, including hostile ones."""

    choice = rng.randrange(5)
    if choice == 0:
        return make_context(capabilities=ProviderCapabilities())
    if choice == 1:
        return make_context(approval=None)
    if choice == 2:
        return make_context(
            approval=Approval(
                intent_id="intent-other", approved_by="operator", approved_at=NOW
            )
        )
    if choice == 3:
        return make_context(open_reservations_for_intent=1)
    return make_context(intent=make_intent(intent_id=intent_id))


def test_random_event_walks_respect_every_invariant() -> None:
    rng = random.Random(20261024)
    events = list(ReservationEvent)
    reservation = Reservation.start(
        reservation_id="rsv-walk", intent_id=make_intent().intent_id, now=NOW
    )
    previous = reservation.state
    visited: set[ReservationState] = {previous}

    for step in range(3000):
        event = rng.choice(events)
        context = _random_context(rng, reservation.intent_id)
        payload = payload_for(event) if rng.random() < 0.7 else TransitionPayload()
        try:
            updated = transition(reservation, event, context, payload)
        except DomainError:
            assert reservation.state is previous
            continue

        assert previous not in TERMINAL_STATES, "a terminal state must never transition"
        assert updated.updated_at >= reservation.updated_at
        assert updated.intent_id == reservation.intent_id
        if updated.state is ReservationState.CONFIRMED:
            assert updated.provider_reference is not None
        if updated.state is ReservationState.REJECTED:
            assert updated.rejection_reason is not None
        reservation = updated
        previous = updated.state
        visited.add(previous)

        if updated.is_terminal:
            # A finished reservation is a dead end by design; start a new walk
            # so the fuzzing keeps exploring the rest of the machine.
            reservation = Reservation.start(
                reservation_id=f"rsv-walk-{step}",
                intent_id=make_intent().intent_id,
                now=NOW,
            )
            previous = reservation.state
            visited.add(previous)

    assert len(visited) >= 5, f"walk covered too little of the machine: {sorted(visited)}"
