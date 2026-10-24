"""Submitting: one intent, one key, one reservation — no matter who asks twice."""

from __future__ import annotations

import re

import pytest

from provider_support import (
    ECONOMY,
    MAIN_SERVICE,
    SECOND_SERVICE,
    VIP,
    make_intent,
    make_provider,
    make_submission,
)
from raja_assistant.adapters.simulator import (
    HOLD_TTL,
    REFERENCE_PREFIX,
    SimulatedReservationProvider,
    SimulatedReservationStatus,
    SimulatorFaults,
)
from raja_assistant.application.ports.errors import (
    DuplicateSubmissionAttempt,
    ProviderContractError,
)
from raja_assistant.application.ports.models import (
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationPending,
    ReservationRejected,
    SubmissionOutcomeUnknown,
)
from raja_assistant.domain.models.service import ServiceId
from support import NOW

REFERENCE_PATTERN = re.compile(rf"{REFERENCE_PREFIX}-[0-9A-F]{{10}}")


def test_a_successful_submission_returns_an_obviously_fake_reference() -> None:
    result = SimulatedReservationProvider().submit(make_submission())

    assert isinstance(result, ReservationConfirmed)
    assert REFERENCE_PATTERN.fullmatch(result.provider_reference)
    assert result.observed_at == NOW


def test_a_successful_submission_is_recorded_and_consumes_seats() -> None:
    provider = SimulatedReservationProvider()
    request = make_submission()

    result = provider.submit(request)

    record = provider.record_for(request.idempotency_key)
    assert record is not None
    assert record.status is SimulatedReservationStatus.CONFIRMED
    assert isinstance(result, ReservationConfirmed)
    assert record.provider_reference == result.provider_reference
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 10


def test_replaying_the_same_key_returns_the_first_answer() -> None:
    provider = SimulatedReservationProvider()
    request = make_submission()

    first = provider.submit(request)
    replay = provider.submit(request)
    third = provider.submit(request)

    assert replay == first
    assert third == first
    assert len(provider.records) == 1
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 10


def test_a_second_key_for_the_same_intent_is_refused() -> None:
    provider = SimulatedReservationProvider()
    provider.submit(make_submission())

    with pytest.raises(DuplicateSubmissionAttempt) as error:
        provider.submit(make_submission(intent=make_intent(), attempt_epoch=2))

    assert error.value.intent_id == "intent-sim-0001"
    assert len(provider.records) == 1


def test_a_new_epoch_cannot_smuggle_in_a_second_booking() -> None:
    """A retry with a fresh key is exactly how duplicate bookings happen."""

    provider = SimulatedReservationProvider()
    provider.submit(make_submission())
    seats_after_first = provider.remaining_seats(MAIN_SERVICE, ECONOMY)

    with pytest.raises(DuplicateSubmissionAttempt):
        provider.submit(make_submission(attempt_epoch=2))

    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == seats_after_first
    assert len(provider.records) == 1


def test_an_unknown_service_is_a_definitive_rejection() -> None:
    result = SimulatedReservationProvider().submit(
        make_submission(intent=make_intent(service_id=ServiceId("sim-9999")))
    )

    assert isinstance(result, ReservationRejected)
    assert result.reason == "unknown_service"


def test_insufficient_seats_is_a_definitive_rejection() -> None:
    provider = SimulatedReservationProvider()
    request = make_submission(
        intent=make_intent(service_id=SECOND_SERVICE, party_size=7),
    )

    result = provider.submit(request)

    assert isinstance(result, ReservationRejected)
    assert result.reason == "sold_out"
    assert provider.records == ()
    assert provider.remaining_seats(SECOND_SERVICE, ECONOMY) == 6


def test_an_unoffered_quota_class_is_a_definitive_rejection() -> None:
    result = SimulatedReservationProvider().submit(
        make_submission(intent=make_intent(quota_class=VIP))
    )

    assert isinstance(result, ReservationRejected)
    assert result.reason == "unsupported_quota_class"


def test_a_rejection_creates_no_record() -> None:
    provider = SimulatedReservationProvider()
    provider.submit(make_submission(intent=make_intent(quota_class=VIP)))

    assert provider.records == ()
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 12


def test_a_submission_outage_books_nothing() -> None:
    provider = make_provider(submit_unavailable=True)

    result = provider.submit(make_submission())

    assert isinstance(result, ProviderUnavailable)
    assert provider.records == ()
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 12


def test_an_injected_rejection_is_reported_as_a_rejection() -> None:
    provider = make_provider(submit_rejected=True)

    result = provider.submit(make_submission())

    assert isinstance(result, ReservationRejected)
    assert result.reason == "simulated_rejection"
    assert provider.records == ()


def test_a_pending_submission_is_recorded_but_not_confirmed() -> None:
    provider = make_provider(submit_pending=True)
    request = make_submission()

    result = provider.submit(request)

    record = provider.record_for(request.idempotency_key)
    assert isinstance(result, ReservationPending)
    assert record is not None
    assert record.status is SimulatedReservationStatus.PENDING
    assert result.client_reference == request.idempotency_key
    # The seats are consumed: the provider did accept the request.
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 10


def test_a_held_submission_reports_a_fixed_expiry() -> None:
    provider = make_provider(submit_holds_seats=True)
    request = make_submission()

    result = provider.submit(request)

    record = provider.record_for(request.idempotency_key)
    assert isinstance(result, ReservationHeld)
    assert record is not None
    assert record.status is SimulatedReservationStatus.HELD
    assert result.hold_expires_at == NOW + HOLD_TTL


def test_contradictory_submit_faults_are_refused() -> None:
    with pytest.raises(ProviderContractError) as error:
        SimulatorFaults(submit_unavailable=True, submit_rejected=True)

    assert error.value.field == "faults"


def test_contradictory_lookup_faults_are_refused() -> None:
    with pytest.raises(ProviderContractError) as error:
        SimulatorFaults(lookup_unavailable=True, lookup_reports_not_found=True)

    assert error.value.field == "faults"


def test_active_faults_lists_every_switch_that_is_on() -> None:
    faults = SimulatorFaults(search_unavailable=True, submit_pending=True)

    assert faults.active_faults == ("search_unavailable", "submit_pending")


def test_the_unknown_outcome_is_not_reported_as_a_confirmation() -> None:
    result = make_provider(submit_outcome_unknown=True).submit(make_submission())

    assert isinstance(result, SubmissionOutcomeUnknown)
    assert not isinstance(result, ReservationConfirmed)


def test_no_submission_answer_claims_a_real_booking() -> None:
    """Every reference a caller can receive is marked as simulated."""

    provider = SimulatedReservationProvider()
    result = provider.submit(make_submission())

    assert isinstance(result, ReservationConfirmed)
    assert result.provider_reference.startswith(f"{REFERENCE_PREFIX}-")
