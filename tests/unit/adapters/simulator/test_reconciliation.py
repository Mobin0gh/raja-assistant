"""Reconciliation is the only way out of an ambiguous submission.

The critical property: the simulator makes a booking that the caller is *not*
told about, and only a lookup with the same key reveals it. No second submission
is ever needed, or accepted.
"""

from __future__ import annotations

import pytest

from provider_support import (
    ECONOMY,
    MAIN_SERVICE,
    SECOND_SERVICE,
    make_intent,
    make_lookup,
    make_provider,
    make_submission,
)
from raja_assistant.adapters.simulator import (
    SimulatedReservationProvider,
    SimulatedReservationStatus,
)
from raja_assistant.application.ports.errors import ProviderContractError
from raja_assistant.application.ports.models import (
    LookupRequest,
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationPending,
    SubmissionOutcomeUnknown,
)
from support import NOW


def test_an_ambiguous_submission_hides_a_reservation_the_simulator_did_make() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    request = make_submission()

    result = provider.submit(request)

    record = provider.record_for(request.idempotency_key)
    assert isinstance(result, SubmissionOutcomeUnknown)
    assert record is not None
    assert record.status is SimulatedReservationStatus.CONFIRMED
    assert record.provider_reference not in str(result)
    # The seats are gone: the simulator really did accept the request.
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == 10


def test_lookup_with_the_same_key_reveals_the_reference() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    request = make_submission()
    provider.submit(request)

    lookup = provider.lookup(make_lookup(request))

    record = provider.record_for(request.idempotency_key)
    assert record is not None
    assert isinstance(lookup, ReservationConfirmed)
    assert lookup.provider_reference == record.provider_reference


def test_replaying_an_ambiguous_submission_stays_ambiguous_and_books_nothing_more() -> None:
    provider = make_provider(submit_outcome_unknown=True)
    request = make_submission()
    first = provider.submit(request)
    seats_after_first = provider.remaining_seats(MAIN_SERVICE, ECONOMY)

    replay = provider.submit(request)

    assert replay == first
    assert isinstance(replay, SubmissionOutcomeUnknown)
    assert len(provider.records) == 1
    assert provider.remaining_seats(MAIN_SERVICE, ECONOMY) == seats_after_first


def test_lookup_of_an_unseen_key_is_definitively_not_found() -> None:
    provider = SimulatedReservationProvider()

    result = provider.lookup(make_lookup(make_submission()))

    assert isinstance(result, ReservationNotFound)
    assert result.client_reference == make_submission().idempotency_key
    assert result.observed_at == NOW


def test_a_lookup_outage_resolves_nothing_and_claims_nothing() -> None:
    provider = make_provider(submit_outcome_unknown=True, lookup_unavailable=True)
    request = make_submission()
    provider.submit(request)

    result = provider.lookup(make_lookup(request))

    assert isinstance(result, ProviderUnavailable)
    assert not isinstance(result, ReservationNotFound)


def test_lookup_can_report_a_lost_record() -> None:
    provider = make_provider(lookup_reports_not_found=True)
    request = make_submission()
    provider.submit(request)

    result = provider.lookup(make_lookup(request))

    assert isinstance(result, ReservationNotFound)
    assert provider.record_for(request.idempotency_key) is not None


def test_lookup_of_a_pending_submission_remains_pending() -> None:
    provider = make_provider(submit_pending=True)
    request = make_submission()
    provider.submit(request)

    assert isinstance(provider.lookup(make_lookup(request)), ReservationPending)


def test_lookup_of_a_held_submission_reports_the_hold() -> None:
    provider = make_provider(submit_holds_seats=True)
    request = make_submission()
    provider.submit(request)

    result = provider.lookup(make_lookup(request))

    record = provider.record_for(request.idempotency_key)
    assert record is not None
    assert isinstance(result, ReservationHeld)
    assert result.hold_expires_at == record.hold_expires_at


def test_lookup_refuses_a_request_for_another_intent() -> None:
    provider = SimulatedReservationProvider()
    request = make_submission()
    provider.submit(request)
    mismatched = LookupRequest(
        idempotency_key=request.idempotency_key,
        intent_id="intent-sim-9999",
        requested_at=NOW,
    )

    with pytest.raises(ProviderContractError) as error:
        provider.lookup(mismatched)

    assert error.value.field == "intent_id"


def test_lookup_only_reports_the_intent_it_was_built_from() -> None:
    """A lookup built from a submission always names the same intent."""

    request = make_submission(intent=make_intent(service_id=SECOND_SERVICE))

    assert make_lookup(request).intent_id == request.intent.intent_id


def test_lookup_never_creates_a_reservation() -> None:
    provider = SimulatedReservationProvider()
    unseen = make_submission()

    provider.lookup(make_lookup(unseen))
    provider.lookup(make_lookup(unseen))

    assert provider.records == ()
