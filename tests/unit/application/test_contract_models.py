"""The provider contract's shapes, and what it refuses to represent.

The important properties are negative ones: a confirmation cannot exist without a
reference, an ambiguous outcome cannot exist without a client reference, and a
request cannot be built with an idempotency key that belongs to something else.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import pytest

from provider_support import make_intent, make_submission
from raja_assistant.application.ports.errors import ProviderContractError
from raja_assistant.application.ports.models import (
    LookupRequest,
    LookupResult,
    ProviderOutcome,
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationPending,
    ReservationRejected,
    SearchResult,
    SearchSucceeded,
    ServiceOffer,
    SubmissionOutcomeUnknown,
    SubmissionRequest,
    SubmissionResult,
)
from raja_assistant.domain.errors import ValidationError
from raja_assistant.domain.models.preferences import QuotaClass, StationCode
from raja_assistant.domain.models.service import (
    Availability,
    AvailabilityStatus,
    ServiceId,
    TrainService,
)
from raja_assistant.domain.reservation.idempotency import KEY_PREFIX, derive_idempotency_key
from support import NOW

TEHRAN = timezone(timedelta(hours=3, minutes=30))

RESULT_TYPES = [
    (SearchSucceeded, ProviderOutcome.FOUND),
    (ReservationConfirmed, ProviderOutcome.CONFIRMED),
    (ReservationHeld, ProviderOutcome.HELD),
    (ReservationPending, ProviderOutcome.PENDING),
    (ReservationRejected, ProviderOutcome.REJECTED),
    (ReservationNotFound, ProviderOutcome.NOT_FOUND),
    (SubmissionOutcomeUnknown, ProviderOutcome.OUTCOME_UNKNOWN),
    (ProviderUnavailable, ProviderOutcome.UNAVAILABLE),
]


def make_service() -> TrainService:
    departs_at = datetime(2026, 11, 1, 20, 30, tzinfo=TEHRAN)
    return TrainService(
        service_id=ServiceId("sim-0001"),
        train_name="Simulated Test 0001",
        origin=StationCode("THR"),
        destination=StationCode("MHD"),
        travel_date=date(2026, 11, 1),
        departs_at=departs_at,
        arrives_at=datetime(2026, 11, 2, 6, 15, tzinfo=TEHRAN),
        fare_irr=4_000_000,
        quota_classes=frozenset({QuotaClass("economy")}),
    )


def make_availability(
    *,
    service_id: ServiceId | None = None,
    quota_class: QuotaClass | None = None,
    seats: int = 4,
) -> Availability:
    service = make_service()
    return Availability(
        service_id=service.service_id if service_id is None else service_id,
        quota_class=QuotaClass("economy") if quota_class is None else quota_class,
        status=AvailabilityStatus.AVAILABLE,
        observed_at=NOW,
        remaining_seats=seats,
    )


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def test_for_intent_derives_the_domain_key() -> None:
    intent = make_intent()
    request = SubmissionRequest.for_intent(intent, submitted_at=NOW)

    assert request.idempotency_key == derive_idempotency_key(
        intent_id=intent.intent_id,
        service_id=intent.service_id,
        travel_date=intent.preferences.travel_date,
        passenger_refs=intent.passenger_refs,
        quota_class=intent.quota_class,
        attempt_epoch=1,
    )


def test_request_refuses_a_key_derived_from_a_different_intent() -> None:
    other = make_submission(intent=make_intent(intent_id="intent-sim-9999"))
    intent = make_intent()

    with pytest.raises(ProviderContractError) as error:
        SubmissionRequest(
            idempotency_key=other.idempotency_key,
            intent=intent,
            attempt_epoch=1,
            submitted_at=NOW,
        )

    assert error.value.field == "idempotency_key"


def test_request_refuses_a_key_outside_the_domain_derivation() -> None:
    with pytest.raises(ProviderContractError) as error:
        SubmissionRequest(
            idempotency_key="hand-written-key",
            intent=make_intent(),
            attempt_epoch=1,
            submitted_at=NOW,
        )

    assert error.value.field == "idempotency_key"


def test_request_requires_the_domain_prefix() -> None:
    with pytest.raises(ProviderContractError) as error:
        SubmissionRequest(
            idempotency_key=f"{KEY_PREFIX}/not-a-digest",
            intent=make_intent(),
            attempt_epoch=1,
            submitted_at=NOW,
        )

    assert error.value.field == "idempotency_key"


@pytest.mark.parametrize("epoch", [0, -3])
def test_request_requires_a_positive_attempt_epoch(epoch: int) -> None:
    """The domain's key derivation is the authority on what a valid attempt is."""

    with pytest.raises(ValidationError) as error:
        make_submission(attempt_epoch=epoch)

    assert error.value.field == "attempt_epoch"


def test_request_requires_an_aware_submission_time() -> None:
    with pytest.raises(ValidationError) as error:
        make_submission(submitted_at=datetime(2026, 10, 24, 12, 0))

    assert error.value.field == "submitted_at"


def test_a_new_epoch_is_a_new_key_for_the_same_intent() -> None:
    intent = make_intent()

    first = SubmissionRequest.for_intent(intent, submitted_at=NOW, attempt_epoch=1)
    second = SubmissionRequest.for_intent(intent, submitted_at=NOW, attempt_epoch=2)

    assert first.idempotency_key != second.idempotency_key


def test_lookup_for_submission_reuses_the_key_and_the_intent() -> None:
    request = make_submission()

    lookup = LookupRequest.for_submission(request, requested_at=NOW)

    assert lookup.idempotency_key == request.idempotency_key
    assert lookup.intent_id == request.intent.intent_id


def test_lookup_requires_an_aware_request_time() -> None:
    request = make_submission()

    with pytest.raises(ValidationError) as error:
        LookupRequest.for_submission(request, requested_at=datetime(2026, 10, 24, 12, 0))

    assert error.value.field == "requested_at"


# --------------------------------------------------------------------------- #
# Offers
# --------------------------------------------------------------------------- #


def test_offer_refuses_availability_for_another_service() -> None:
    service = make_service()

    with pytest.raises(ProviderContractError) as error:
        ServiceOffer(
            service=service,
            availability=(make_availability(service_id=ServiceId("sim-9999")),),
        )

    assert error.value.field == "availability"


def test_offer_refuses_a_repeated_quota_class() -> None:
    service = make_service()

    with pytest.raises(ProviderContractError) as error:
        ServiceOffer(service=service, availability=(make_availability(), make_availability()))

    assert error.value.field == "availability"


def test_offer_refuses_to_exist_without_availability() -> None:
    with pytest.raises(ProviderContractError) as error:
        ServiceOffer(service=make_service(), availability=())

    assert error.value.field == "availability"


def test_offer_reports_availability_per_class() -> None:
    service = make_service()
    economy = make_availability()
    offer = ServiceOffer(service=service, availability=(economy,))

    assert offer.is_bookable
    assert offer.availability_for(QuotaClass("economy")) is economy
    assert offer.availability_for(QuotaClass("business")) is None
    assert offer.quota_classes == (QuotaClass("economy"),)


def test_offer_is_not_bookable_when_every_class_is_sold_out() -> None:
    service = make_service()
    sold_out = Availability(
        service_id=service.service_id,
        quota_class=QuotaClass("economy"),
        status=AvailabilityStatus.SOLD_OUT,
        observed_at=NOW,
        remaining_seats=0,
    )

    assert not ServiceOffer(service=service, availability=(sold_out,)).is_bookable


def test_search_may_succeed_with_no_offers() -> None:
    result = SearchSucceeded(offers=(), observed_at=NOW)

    assert result.offers == ()
    assert result.outcome is ProviderOutcome.FOUND


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("result_type", "outcome"), RESULT_TYPES, ids=lambda value: str(value))
def test_every_result_declares_its_outcome(result_type: type, outcome: ProviderOutcome) -> None:
    assert result_type.outcome is outcome


def test_the_outcome_vocabulary_has_no_gaps_and_no_duplicates() -> None:
    declared = [outcome for _, outcome in RESULT_TYPES]

    assert len(set(declared)) == len(declared)
    assert set(declared) == set(ProviderOutcome)


def test_confirmation_cannot_exist_without_a_reference() -> None:
    with pytest.raises(ProviderContractError) as error:
        ReservationConfirmed(provider_reference="   ", observed_at=NOW)

    assert error.value.field == "provider_reference"


def test_confirmation_cannot_be_built_without_the_argument() -> None:
    with pytest.raises(TypeError):
        ReservationConfirmed(observed_at=NOW)  # type: ignore[call-arg]


def test_ambiguity_cannot_exist_without_a_client_reference() -> None:
    with pytest.raises(ProviderContractError) as error:
        SubmissionOutcomeUnknown(client_reference="", observed_at=NOW)

    assert error.value.field == "client_reference"


def test_rejection_cannot_exist_without_a_reason() -> None:
    with pytest.raises(ProviderContractError) as error:
        ReservationRejected(reason=" ", observed_at=NOW)

    assert error.value.field == "reason"


@pytest.mark.parametrize(
    "build",
    [
        lambda: SearchSucceeded(offers=(), observed_at=datetime(2026, 10, 24, 12, 0)),
        lambda: ReservationConfirmed(
            provider_reference="SIM-1", observed_at=datetime(2026, 10, 24, 12, 0)
        ),
        lambda: ReservationPending(
            client_reference="key", observed_at=datetime(2026, 10, 24, 12, 0)
        ),
        lambda: ReservationNotFound(
            client_reference="key", observed_at=datetime(2026, 10, 24, 12, 0)
        ),
        lambda: ProviderUnavailable(observed_at=datetime(2026, 10, 24, 12, 0)),
    ],
    ids=["search", "confirmed", "pending", "not_found", "unavailable"],
)
def test_results_refuse_naive_timestamps(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        build()


def test_submission_results_accept_their_members() -> None:
    confirmed = ReservationConfirmed(provider_reference="SIM-1", observed_at=NOW)

    assert isinstance(confirmed, SubmissionResult)
    assert isinstance(confirmed, LookupResult)
    assert isinstance(SearchSucceeded(offers=(), observed_at=NOW), SearchResult)


def test_lookup_only_results_are_not_submission_results() -> None:
    not_found = ReservationNotFound(client_reference="key", observed_at=NOW)

    assert isinstance(not_found, LookupResult)
    assert not isinstance(not_found, SubmissionResult)


def test_submission_only_results_are_not_lookup_results() -> None:
    unknown = SubmissionOutcomeUnknown(client_reference="key", observed_at=NOW)

    assert isinstance(unknown, SubmissionResult)
    assert not isinstance(unknown, LookupResult)
