"""Simulator results must be able to drive the real state machine.

The translation from a provider result to a reservation event is written here, not
in the package: producing it is orchestration work (a later phase). Writing it as a
test proves that the contract's vocabulary is sufficient for every edge the machine
needs, and that the machine's guards still hold when the results come from an
adapter rather than a hand-built payload.

An ambiguous answer can only reach ``CONFIRMED`` or ``REJECTED`` through
reconciliation, which is exactly the safety property P1 established.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from provider_support import (
    ECONOMY,
    MAIN_SERVICE,
    VIP,
    make_intent,
    make_lookup,
    make_provider,
    make_submission,
)
from raja_assistant.adapters.simulator import SimulatedReservationProvider
from raja_assistant.application.ports.models import (
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationRejected,
    SubmissionOutcomeUnknown,
)
from raja_assistant.domain.errors import GuardReason, GuardViolation, IllegalTransition
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.reservation.events import ReservationEvent, TransitionPayload
from raja_assistant.domain.reservation.guards import ProviderCapabilities
from raja_assistant.domain.reservation.states import ReservationState
from raja_assistant.domain.reservation.transitions import transition
from support import NOW, make_context

LATER = NOW + timedelta(minutes=5)


def submission_effect(result: object) -> tuple[ReservationEvent, TransitionPayload]:
    """Translate a submission result into the event the machine should apply."""

    if isinstance(result, ReservationConfirmed):
        return (
            ReservationEvent.ACK_CONFIRMED,
            TransitionPayload(provider_reference=result.provider_reference),
        )
    if isinstance(result, ReservationHeld):
        return (
            ReservationEvent.ACK_HELD,
            TransitionPayload(hold_expires_at=result.hold_expires_at),
        )
    if isinstance(result, ReservationRejected):
        return (
            ReservationEvent.ACK_REJECTED,
            TransitionPayload(rejection_reason=result.reason),
        )
    if isinstance(result, SubmissionOutcomeUnknown):
        return ReservationEvent.ACK_UNKNOWN, TransitionPayload()
    if isinstance(result, ProviderUnavailable):
        return ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT, TransitionPayload()
    raise AssertionError(f"no acknowledgement exists for {result!r}")


def reconciliation_effect(result: object) -> tuple[ReservationEvent, TransitionPayload]:
    """Translate a lookup result into the event that resolves ambiguity."""

    if isinstance(result, ReservationConfirmed):
        return (
            ReservationEvent.RECONCILE_BOOKED,
            TransitionPayload(
                provider_reference=result.provider_reference,
                evidence_note="simulated lookup by client reference",
            ),
        )
    if isinstance(result, ReservationNotFound):
        return (
            ReservationEvent.RECONCILE_NOT_BOOKED,
            TransitionPayload(evidence_note="simulated lookup found no reservation"),
        )
    if isinstance(result, ProviderUnavailable):
        return (
            ReservationEvent.RECONCILE_INCONCLUSIVE,
            TransitionPayload(evidence_note="simulated lookup outage"),
        )
    raise AssertionError(f"no reconciliation exists for {result!r}")


def submitting(provider: SimulatedReservationProvider) -> Reservation:
    """Drive a reservation into ``SUBMITTING`` through the real transition table."""

    intent = make_intent()
    context = make_context(now=NOW, intent=intent, capabilities=provider.capabilities)
    reservation = Reservation.start(
        reservation_id="rsv-sim-0001",
        intent_id=intent.intent_id,
        now=NOW,
    )
    reservation = transition(reservation, ReservationEvent.VALIDATE, context)
    reservation = transition(reservation, ReservationEvent.REQUEST_APPROVAL, context)
    return transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)


def unknown_reservation(provider: SimulatedReservationProvider) -> Reservation:
    """Park a reservation in ``SUBMISSION_UNKNOWN`` the way the orchestrator would."""

    context = make_context(now=NOW, capabilities=provider.capabilities)
    return transition(submitting(provider), ReservationEvent.ACK_UNKNOWN, context)


def test_a_simulated_confirmation_reaches_the_confirmed_state() -> None:
    provider = SimulatedReservationProvider()
    reservation = submitting(provider)
    result = provider.submit(make_submission())

    event, payload = submission_effect(result)
    context = make_context(now=NOW, capabilities=provider.capabilities)
    confirmed = transition(reservation, event, context, payload)

    assert isinstance(result, ReservationConfirmed)
    assert confirmed.state is ReservationState.CONFIRMED
    assert confirmed.provider_reference == result.provider_reference
    assert confirmed.is_terminal


def test_a_simulated_hold_reaches_the_held_state() -> None:
    provider = make_provider(submit_holds_seats=True)
    reservation = submitting(provider)
    result = provider.submit(make_submission())

    event, payload = submission_effect(result)
    context = make_context(now=NOW, capabilities=provider.capabilities)
    held = transition(reservation, event, context, payload)

    assert isinstance(result, ReservationHeld)
    assert held.state is ReservationState.HELD
    assert held.hold_expires_at == result.hold_expires_at


def test_a_simulated_rejection_reaches_the_rejected_state() -> None:
    provider = SimulatedReservationProvider()
    reservation = submitting(provider)
    result = provider.submit(make_submission(intent=make_intent(quota_class=VIP)))

    event, payload = submission_effect(result)
    rejected = transition(
        reservation, event, make_context(now=NOW, capabilities=provider.capabilities), payload
    )

    assert isinstance(result, ReservationRejected)
    assert rejected.state is ReservationState.REJECTED
    assert rejected.rejection_reason == result.reason


def test_an_ambiguous_answer_parks_the_reservation_and_forbids_confirmation() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    result = provider.submit(make_submission())

    unknown = unknown_reservation(provider)

    assert isinstance(result, SubmissionOutcomeUnknown)
    assert unknown.state is ReservationState.SUBMISSION_UNKNOWN
    assert not unknown.is_terminal
    with pytest.raises(IllegalTransition):
        transition(
            unknown,
            ReservationEvent.ACK_CONFIRMED,
            make_context(now=LATER, capabilities=provider.capabilities),
            TransitionPayload(provider_reference="SIM-ANYTHING"),
        )


def test_reconciliation_confirms_with_the_lookup_reference() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    request = make_submission()
    provider.submit(request)
    unknown = unknown_reservation(provider)

    event, evidence = reconciliation_effect(provider.lookup(make_lookup(request)))
    confirmed = transition(
        unknown,
        event,
        make_context(now=LATER, capabilities=provider.capabilities),
        evidence,
    )

    record = provider.record_for(request.idempotency_key)
    assert record is not None
    assert confirmed.state is ReservationState.CONFIRMED
    assert confirmed.provider_reference == record.provider_reference


def test_a_conclusive_not_found_rejects_the_reservation() -> None:
    provider = make_provider(submit_outcome_unknown=True, lookup_reports_not_found=True)
    request = make_submission()
    provider.submit(request)
    unknown = unknown_reservation(provider)

    event, evidence = reconciliation_effect(provider.lookup(make_lookup(request)))
    rejected = transition(
        unknown,
        event,
        make_context(now=LATER, capabilities=provider.capabilities),
        evidence,
    )

    assert rejected.state is ReservationState.REJECTED
    assert rejected.evidence_note is not None


def test_an_inconclusive_lookup_leaves_the_reservation_unknown() -> None:
    provider = make_provider(submit_outcome_unknown=True, lookup_unavailable=True)
    request = make_submission()
    provider.submit(request)
    unknown = unknown_reservation(provider)

    event, evidence = reconciliation_effect(provider.lookup(make_lookup(request)))
    still_unknown = transition(
        unknown,
        event,
        make_context(now=LATER, capabilities=provider.capabilities),
        evidence,
    )

    assert still_unknown.state is ReservationState.SUBMISSION_UNKNOWN
    assert still_unknown.evidence_note == "simulated lookup outage"
    assert still_unknown.updated_at == LATER
    assert len(provider.records) == 1


def test_reconciliation_requires_evidence() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    provider.submit(make_submission())
    unknown = unknown_reservation(provider)

    with pytest.raises(GuardViolation) as error:
        transition(
            unknown,
            ReservationEvent.RECONCILE_BOOKED,
            make_context(now=LATER, capabilities=provider.capabilities),
            TransitionPayload(provider_reference="SIM-ANYTHING"),
        )

    assert error.value.reason is GuardReason.EVIDENCE_MISSING


def test_a_provider_outage_sends_the_reservation_back_for_approval() -> None:
    provider = make_provider(submit_unavailable=True)
    reservation = submitting(provider)
    result = provider.submit(make_submission())

    event, payload = submission_effect(result)
    returned = transition(
        reservation, event, make_context(now=NOW, capabilities=provider.capabilities), payload
    )

    assert returned.state is ReservationState.AWAITING_APPROVAL
    assert provider.records == ()


def test_the_domain_guard_refuses_an_unauthorized_adapter() -> None:
    provider = SimulatedReservationProvider(
        capabilities=ProviderCapabilities(authorized=False, book=True)
    )
    intent = make_intent()
    context = make_context(now=NOW, intent=intent, capabilities=provider.capabilities)
    reservation = Reservation.start(
        reservation_id="rsv-sim-0002",
        intent_id=intent.intent_id,
        now=NOW,
    )
    reservation = transition(reservation, ReservationEvent.VALIDATE, context)
    reservation = transition(reservation, ReservationEvent.REQUEST_APPROVAL, context)

    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)

    assert error.value.reason is GuardReason.PROVIDER_NOT_AUTHORIZED
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 12
