"""Search is deterministic, filtered by the query, and honest about sold-out seats."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from provider_support import BUSINESS, DAY_ONE, ECONOMY, make_provider
from raja_assistant.adapters.simulator import SimulatedReservationProvider
from raja_assistant.application.ports.models import (
    ProviderUnavailable,
    SearchResult,
    SearchSucceeded,
    ServiceOffer,
)
from raja_assistant.domain.errors import ValidationError
from raja_assistant.domain.models.preferences import QuotaClass, StationCode
from raja_assistant.domain.models.service import AvailabilityStatus, ServiceId
from support import NOW, make_preferences

THR = StationCode("THR")
MHD = StationCode("MHD")


def search(
    provider: SimulatedReservationProvider | None = None,
    **overrides: object,
) -> SearchResult:
    provider = provider if provider is not None else SimulatedReservationProvider()
    return provider.search(make_preferences(**overrides), now=NOW)


def offers_by_id(result: SearchResult) -> dict[str, ServiceOffer]:
    assert isinstance(result, SearchSucceeded)
    return {offer.service.service_id.value: offer for offer in result.offers}


def test_search_is_deterministic() -> None:
    assert search() == search()


def test_search_orders_offers_by_departure() -> None:
    result = search()

    assert isinstance(result, SearchSucceeded)
    assert [offer.service.service_id.value for offer in result.offers] == [
        "sim-1003",
        "sim-1001",
        "sim-1002",
    ]


def test_search_reports_the_fixture_seat_counts() -> None:
    offers = offers_by_id(search())

    economy = offers["sim-1001"].availability_for(ECONOMY)
    assert economy is not None
    assert economy.status is AvailabilityStatus.AVAILABLE
    assert economy.remaining_seats == 12


def test_search_filters_by_route() -> None:
    offers = offers_by_id(search(origin=MHD, destination=THR))

    assert set(offers) == {"sim-1201"}


def test_search_filters_by_travel_date() -> None:
    offers = offers_by_id(search(travel_date=date(2026, 11, 2)))

    assert set(offers) == {"sim-1101"}


def test_search_returns_only_the_requested_quota_classes() -> None:
    offers = offers_by_id(search(quota_classes=frozenset({BUSINESS})))

    assert "sim-1001" in offers
    assert offers["sim-1001"].quota_classes == (BUSINESS,)


def test_search_returns_every_class_when_none_are_requested() -> None:
    offers = offers_by_id(search(quota_classes=frozenset()))

    assert offers["sim-1001"].quota_classes == (BUSINESS, ECONOMY)


def test_search_drops_services_that_offer_none_of_the_requested_classes() -> None:
    offers = offers_by_id(search(quota_classes=frozenset({QuotaClass("vip")})))

    assert offers == {}


def test_search_reports_sold_out_classes_without_hiding_the_service() -> None:
    offers = offers_by_id(search())

    sold_out = offers["sim-1003"].availability_for(ECONOMY)
    assert sold_out is not None
    assert sold_out.status is AvailabilityStatus.SOLD_OUT
    assert sold_out.remaining_seats == 0
    assert not offers["sim-1003"].is_bookable
    assert offers["sim-1001"].is_bookable


def test_search_reports_an_unknown_route_as_an_empty_success() -> None:
    result = search(destination=StationCode("SFA"))

    assert isinstance(result, SearchSucceeded)
    assert result.offers == ()
    assert result.outcome.value == "found"


def test_search_reports_an_outage_explicitly() -> None:
    result = search(make_provider(search_unavailable=True))

    assert isinstance(result, ProviderUnavailable)
    assert result.observed_at == NOW


def test_search_requires_an_aware_clock() -> None:
    provider = SimulatedReservationProvider()

    with pytest.raises(ValidationError) as error:
        provider.search(make_preferences(), now=datetime(2026, 10, 24, 12, 0))

    assert error.value.field == "now"


def test_search_never_consumes_seats() -> None:
    provider = SimulatedReservationProvider()

    provider.search(make_preferences(), now=NOW)

    assert provider.remaining_seats(ServiceId("sim-1001"), ECONOMY) == 12


def test_search_covers_the_travel_date_of_each_fixture_offer() -> None:
    result = search(travel_date=DAY_ONE)

    assert isinstance(result, SearchSucceeded)
    assert {offer.service.travel_date for offer in result.offers} == {DAY_ONE}
