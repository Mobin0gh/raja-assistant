"""Two fresh simulators given the same calls must agree, always."""

from __future__ import annotations

from datetime import timedelta

from provider_support import ECONOMY, MAIN_SERVICE, make_intent, make_lookup, make_submission
from raja_assistant.adapters.simulator import SimulatedReservationProvider
from raja_assistant.application.ports.models import ReservationConfirmed, SearchSucceeded
from support import NOW, make_preferences

LATER = NOW + timedelta(hours=1)


def test_two_providers_produce_identical_searches() -> None:
    preferences = make_preferences()

    first = SimulatedReservationProvider().search(preferences, now=NOW)
    second = SimulatedReservationProvider().search(preferences, now=NOW)

    assert first == second


def test_two_providers_produce_identical_answers_and_references() -> None:
    request = make_submission()

    first = SimulatedReservationProvider().submit(request)
    second = SimulatedReservationProvider().submit(request)

    assert first == second


def test_repeated_lookups_are_stable() -> None:
    provider = SimulatedReservationProvider()
    request = make_submission()
    provider.submit(request)

    first = provider.lookup(make_lookup(request))
    second = provider.lookup(make_lookup(request))

    assert first == second


def test_instances_do_not_share_seats() -> None:
    busy = SimulatedReservationProvider()
    fresh = SimulatedReservationProvider()

    busy.submit(make_submission())

    assert busy.remaining_seats(MAIN_SERVICE, ECONOMY) == 10
    assert fresh.remaining_seats(MAIN_SERVICE, ECONOMY) == 12


def test_records_do_not_leak_between_instances() -> None:
    busy = SimulatedReservationProvider()
    busy.submit(make_submission())

    assert busy.records != ()
    assert SimulatedReservationProvider().records == ()


def test_search_only_depends_on_the_supplied_clock() -> None:
    provider = SimulatedReservationProvider()
    preferences = make_preferences()

    early = provider.search(preferences, now=NOW)
    late = provider.search(preferences, now=LATER)

    assert isinstance(early, SearchSucceeded)
    assert isinstance(late, SearchSucceeded)
    assert late.observed_at == LATER

    # The offers themselves carry the observation time, so compare the content
    # that must not depend on the clock.
    def snapshot(result: SearchSucceeded) -> list[tuple[str, int, str, int | None]]:
        return [
            (
                offer.service.service_id.value,
                offer.service.fare_irr,
                availability.status.value,
                availability.remaining_seats,
            )
            for offer in result.offers
            for availability in offer.availability
        ]

    assert snapshot(early) == snapshot(late)


def test_different_intents_get_different_references() -> None:
    first = SimulatedReservationProvider().submit(make_submission(intent=make_intent()))
    second = SimulatedReservationProvider().submit(
        make_submission(intent=make_intent(intent_id="intent-sim-0002"))
    )

    assert isinstance(first, ReservationConfirmed)
    assert isinstance(second, ReservationConfirmed)
    assert first.provider_reference != second.provider_reference
