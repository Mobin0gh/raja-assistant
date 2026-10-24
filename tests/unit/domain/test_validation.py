"""Domain validation: every constructor refuses data that breaks an invariant."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from raja_assistant.domain.errors import ValidationError
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, StationCode
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.models.service import (
    Availability,
    AvailabilityStatus,
    ServiceId,
    TrainService,
)
from raja_assistant.domain.reservation.states import ReservationState
from support import (
    MHD,
    NOW,
    THR,
    make_capabilities,
    make_intent,
    make_preferences,
)


@pytest.mark.parametrize("value", ["", "T", "THR-1", "T1!", "ABCDEFGHIJK", "  "])
def test_station_code_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError) as error:
        StationCode(value)
    assert error.value.field == "station_code"


@pytest.mark.parametrize("value", ["", "eco nomy", "x" * 33, "بیزینس"])
def test_quota_class_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError) as error:
        QuotaClass(value)
    assert error.value.field == "quota_class"


def test_station_and_quota_values_are_normalized() -> None:
    assert StationCode(" thr ").value == "THR"
    assert QuotaClass(" Economy ").value == "economy"


@pytest.mark.parametrize("token", ["", "abc", "pax aaaa", "a" * 65, "pax/aaaa"])
def test_passenger_ref_rejects_invalid_tokens(token: str) -> None:
    with pytest.raises(ValidationError) as error:
        PassengerRef(token)
    assert error.value.field == "passenger_token"


def test_preferences_require_distinct_origin_and_destination() -> None:
    with pytest.raises(ValidationError) as error:
        make_preferences(destination=THR)
    assert error.value.field == "destination"


@pytest.mark.parametrize("party_size", [0, 11, -1])
def test_preferences_bound_party_size(party_size: int) -> None:
    with pytest.raises(ValidationError) as error:
        make_preferences(party_size=party_size)
    assert error.value.field == "party_size"


def test_preferences_reject_non_positive_max_fare() -> None:
    with pytest.raises(ValidationError) as error:
        make_preferences(max_fare_irr=0)
    assert error.value.field == "max_fare_irr"


def test_preferences_reject_reversed_departure_window() -> None:
    with pytest.raises(ValidationError) as error:
        make_preferences(earliest_departure=time(22, 0), latest_departure=time(18, 0))
    assert error.value.field == "departure_window"


def _service(**overrides: object) -> TrainService:
    base: dict[str, object] = {
        "service_id": ServiceId("svc-1001"),
        "train_name": "Noor",
        "origin": THR,
        "destination": MHD,
        "travel_date": date(2026, 11, 1),
        "departs_at": datetime(2026, 11, 1, 20, 0, tzinfo=NOW.tzinfo),
        "arrives_at": datetime(2026, 11, 2, 6, 0, tzinfo=NOW.tzinfo),
        "fare_irr": 4_500_000,
        "quota_classes": frozenset({QuotaClass("economy")}),
    }
    base.update(overrides)
    return TrainService(**base)  # type: ignore[arg-type]


def test_valid_service_is_accepted() -> None:
    service = _service()
    assert service.fare_irr == 4_500_000


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"arrives_at": datetime(2026, 11, 1, 19, 0, tzinfo=NOW.tzinfo)}, "arrives_at"),
        ({"departs_at": datetime(2026, 11, 1, 20, 0)}, "departs_at"),
        ({"travel_date": date(2026, 11, 2)}, "travel_date"),
        ({"fare_irr": 0}, "fare_irr"),
        ({"quota_classes": frozenset()}, "quota_classes"),
        ({"destination": THR}, "destination"),
        ({"train_name": "   "}, "train_name"),
    ],
)
def test_service_rejects_invalid_values(overrides: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError) as error:
        _service(**overrides)
    assert error.value.field == field


@pytest.mark.parametrize(
    ("status", "remaining", "field"),
    [
        (AvailabilityStatus.AVAILABLE, 0, "remaining_seats"),
        (AvailabilityStatus.AVAILABLE, None, "remaining_seats"),
        (AvailabilityStatus.SOLD_OUT, 5, "remaining_seats"),
        (AvailabilityStatus.SOLD_OUT, None, "remaining_seats"),
        (AvailabilityStatus.UNKNOWN, 3, "remaining_seats"),
    ],
)
def test_availability_status_must_match_remaining_seats(
    status: AvailabilityStatus, remaining: int | None, field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        Availability(
            service_id=ServiceId("svc-1001"),
            quota_class=QuotaClass("economy"),
            status=status,
            observed_at=NOW,
            remaining_seats=remaining,
        )
    assert error.value.field == field


def test_availability_unknown_is_not_bookable() -> None:
    availability = Availability(
        service_id=ServiceId("svc-1001"),
        quota_class=QuotaClass("economy"),
        status=AvailabilityStatus.UNKNOWN,
        observed_at=NOW,
    )
    assert availability.is_bookable is False


def test_availability_requires_aware_observation_time() -> None:
    with pytest.raises(ValidationError) as error:
        Availability(
            service_id=ServiceId("svc-1001"),
            quota_class=QuotaClass("economy"),
            status=AvailabilityStatus.UNKNOWN,
            observed_at=datetime(2026, 10, 24, 12, 0),
        )
    assert error.value.field == "observed_at"


def test_intent_requires_passenger_refs_matching_party_size() -> None:
    with pytest.raises(ValidationError) as error:
        make_intent(passenger_refs=(PassengerRef("pax-aaaa"),))
    assert error.value.field == "passenger_refs"


def test_intent_requires_unique_passengers() -> None:
    with pytest.raises(ValidationError) as error:
        make_intent(passenger_refs=(PassengerRef("pax-aaaa"), PassengerRef("pax-aaaa")))
    assert error.value.field == "passenger_refs"


def test_intent_fare_must_not_exceed_traveller_maximum() -> None:
    with pytest.raises(ValidationError) as error:
        make_intent(fare_irr=99_000_000)
    assert error.value.field == "fare_irr"


def test_intent_quote_must_expire_after_creation() -> None:
    with pytest.raises(ValidationError) as error:
        make_intent(quote_expires_at=NOW)
    assert error.value.field == "quote_expires_at"


def test_intent_requires_requested_quota_class() -> None:
    with pytest.raises(ValidationError) as error:
        make_intent(quota_class=QuotaClass("business"))
    assert error.value.field == "quota_class"


def test_preferences_allow_empty_quota_class_filter() -> None:
    preferences = make_preferences(quota_classes=frozenset())
    intent = make_intent(preferences=preferences, quota_class=QuotaClass("business"))
    assert preferences.quota_classes == frozenset()
    assert intent.quota_class == QuotaClass("business")


def test_confirmed_reservation_requires_provider_reference() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.CONFIRMED,
            attempt_epoch=1,
            created_at=NOW,
            updated_at=NOW,
        )
    assert error.value.field == "provider_reference"


def test_rejected_reservation_requires_reason() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.REJECTED,
            attempt_epoch=1,
            created_at=NOW,
            updated_at=NOW,
        )
    assert error.value.field == "rejection_reason"


def test_held_reservation_requires_hold_expiry() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.HELD,
            attempt_epoch=1,
            created_at=NOW,
            updated_at=NOW,
        )
    assert error.value.field == "hold_expires_at"


def test_pre_submission_reservation_cannot_carry_provider_reference() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.DRAFT,
            attempt_epoch=1,
            created_at=NOW,
            updated_at=NOW,
            provider_reference="PNR-1",
        )
    assert error.value.field == "provider_reference"


def test_reservation_timestamps_must_move_forward() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.DRAFT,
            attempt_epoch=1,
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )
    assert error.value.field == "updated_at"


def test_attempt_epoch_must_be_positive() -> None:
    with pytest.raises(ValidationError) as error:
        Reservation(
            reservation_id="rsv-1",
            intent_id="intent-1",
            state=ReservationState.DRAFT,
            attempt_epoch=0,
            created_at=NOW,
            updated_at=NOW,
        )
    assert error.value.field == "attempt_epoch"


def test_reservation_start_is_draft() -> None:
    reservation = Reservation.start(reservation_id="rsv-1", intent_id="intent-1", now=NOW)
    assert reservation.state is ReservationState.DRAFT
    assert reservation.attempt_epoch == 1
    assert reservation.is_terminal is False


def test_default_capabilities_grant_nothing() -> None:
    from raja_assistant.domain.reservation.guards import ProviderCapabilities

    capabilities = ProviderCapabilities()
    assert not any(
        (
            capabilities.authorized,
            capabilities.book,
            capabilities.hold,
            capabilities.pay,
            capabilities.lookup_by_reference,
        )
    )


def test_make_capabilities_helper_is_permissive() -> None:
    assert make_capabilities().authorized is True
