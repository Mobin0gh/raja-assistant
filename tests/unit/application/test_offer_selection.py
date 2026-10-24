"""Offer eligibility is traveller policy: fares are per passenger, no clock runs."""

from __future__ import annotations

from provider_support import (
    ECONOMY,
    MAIN_SERVICE,
    NEXT_DAY_SERVICE,
    RETURN_SERVICE,
    SECOND_SERVICE,
    SOLD_OUT_ECONOMY_SERVICE,
    make_provider,
)
from raja_assistant.adapters.simulator.fixtures import BUILT_IN_CATALOGUE
from raja_assistant.application.offers import bookable_class, eligible_offers, is_eligible
from raja_assistant.application.ports.models import ServiceOffer
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences
from raja_assistant.domain.models.service import Availability, AvailabilityStatus, ServiceId
from service_support import fixture_service, offer_for
from support import NOW, make_preferences


def _offers(preferences: TripPreferences) -> tuple[ServiceOffer, ...]:
    result = make_provider().search(preferences, now=NOW)
    return result.offers  # type: ignore[union-attr]


def test_default_preferences_keep_the_available_services() -> None:
    offers = eligible_offers(make_preferences(), _offers(make_preferences()))

    identifiers = [offer.service.service_id for offer in offers]
    assert identifiers == [MAIN_SERVICE, SECOND_SERVICE]


def test_a_sold_out_class_is_not_offered() -> None:
    offers = eligible_offers(make_preferences(), _offers(make_preferences()))

    assert SOLD_OUT_ECONOMY_SERVICE not in [offer.service.service_id for offer in offers]


def test_the_fare_ceiling_is_per_passenger() -> None:
    """4.0M admits the 3.9M service but not the 4.5M one — never a party total."""

    preferences = make_preferences(max_fare_irr=4_000_000)
    offers = eligible_offers(preferences, _offers(preferences))

    assert [offer.service.service_id for offer in offers] == [SECOND_SERVICE]
    assert offers[0].service.fare_irr == 3_900_000


def test_the_departure_window_filters_services() -> None:
    preferences = make_preferences(earliest_departure=None, latest_departure=None)
    base = eligible_offers(preferences, _offers(preferences))
    assert len(base) > 1

    preferences = make_preferences(
        earliest_departure=__import__("datetime").time(21, 0),
        latest_departure=None,
    )
    offers = eligible_offers(preferences, _offers(preferences))

    assert [offer.service.service_id for offer in offers] == [SECOND_SERVICE]


def test_a_larger_party_must_fit_in_one_class() -> None:
    preferences = make_preferences(party_size=7)
    offers = eligible_offers(preferences, _offers(preferences))

    assert [offer.service.service_id for offer in offers] == [MAIN_SERVICE]


def test_the_route_and_date_must_match() -> None:
    return_preferences = make_preferences(
        origin=RETURN_SERVICE and fixture_service(RETURN_SERVICE).service.origin,
        destination=fixture_service(RETURN_SERVICE).service.destination,
    )
    offers = eligible_offers(return_preferences, _offers(return_preferences))

    assert [offer.service.service_id for offer in offers] == [RETURN_SERVICE]

    wrong_day = make_preferences(travel_date=fixture_service(NEXT_DAY_SERVICE).service.travel_date)
    offers = eligible_offers(wrong_day, _offers(wrong_day))
    assert [offer.service.service_id for offer in offers] == [NEXT_DAY_SERVICE]


def test_an_unobserved_availability_is_not_bookable() -> None:
    service = fixture_service(MAIN_SERVICE).service
    offer = ServiceOffer(
        service=service,
        availability=(
            Availability(
                service_id=service.service_id,
                quota_class=QuotaClass("economy"),
                status=AvailabilityStatus.UNKNOWN,
                observed_at=NOW,
                remaining_seats=None,
            ),
        ),
    )

    assert not is_eligible(offer, make_preferences())
    assert bookable_class(offer, make_preferences()) is None


def test_results_are_ordered_by_departure_however_they_arrive() -> None:
    preferences = make_preferences()
    offers = _offers(preferences)

    ordered = eligible_offers(preferences, offers)
    reversed_order = eligible_offers(preferences, tuple(reversed(offers)))

    assert ordered == reversed_order
    assert [offer.service.departs_at for offer in ordered] == sorted(
        offer.service.departs_at for offer in ordered
    )


def test_bookable_class_returns_a_requested_class_with_enough_seats() -> None:
    preferences = make_preferences()

    assert bookable_class(offer_for(preferences), preferences) == ECONOMY

    # Test insufficient seats: sim-1002 has only 6 economy seats available.
    sim1002 = next(s for s in BUILT_IN_CATALOGUE if s.service.service_id.value == "sim-1002")
    tight_prefs = make_preferences(party_size=7)
    tight_offer = ServiceOffer(
        service=sim1002.service,
        availability=(
            Availability(
                service_id=ServiceId("sim-1002"),
                quota_class=QuotaClass("economy"),
                status=AvailabilityStatus.AVAILABLE,
                observed_at=NOW,
                remaining_seats=6,
            ),
        ),
    )
    assert bookable_class(tight_offer, tight_prefs) is None
