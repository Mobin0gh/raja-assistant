"""Traveller policy: which offers are worth showing, and which may be quoted.

Fare and departure-window matching belong to the traveller, not the provider, so
they are applied here rather than inside an adapter. The rules are pure and
deterministic; nothing reads a clock.

``TrainService.fare_irr`` and ``ReservationIntent.fare_irr`` are **per-passenger**
values. ``max_fare_irr`` is therefore a per-passenger ceiling, both when filtering
offers and when the domain validates an intent; the party total is derived on the
record (``ReservationRecord.total_fare_irr``) and is never stored.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import time

from raja_assistant.application.ports.models import ServiceOffer
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences


def _within_departure_window(departure: time, preferences: TripPreferences) -> bool:
    """Compare against the service's own local wall clock, as the adapter reported it."""

    if preferences.earliest_departure is not None and departure < preferences.earliest_departure:
        return False
    return not (
        preferences.latest_departure is not None
        and departure > preferences.latest_departure
    )


def _wanted_classes(preferences: TripPreferences, offer: ServiceOffer) -> tuple[QuotaClass, ...]:
    if preferences.quota_classes:
        return tuple(sorted(preferences.quota_classes))
    return offer.quota_classes


def bookable_class(offer: ServiceOffer, preferences: TripPreferences) -> QuotaClass | None:
    """Return the first wanted class with enough seats for the whole party."""

    for quota_class in _wanted_classes(preferences, offer):
        availability = offer.availability_for(quota_class)
        if availability is None or not availability.is_bookable:
            continue
        if availability.remaining_seats is None:
            continue
        if availability.remaining_seats >= preferences.party_size:
            return quota_class
    return None


def is_eligible(offer: ServiceOffer, preferences: TripPreferences) -> bool:
    """Return True when ``offer`` matches every traveller requirement."""

    service = offer.service
    if service.origin != preferences.origin:
        return False
    if service.destination != preferences.destination:
        return False
    if service.travel_date != preferences.travel_date:
        return False
    if not _within_departure_window(service.departs_at.time(), preferences):
        return False
    if preferences.max_fare_irr is not None and service.fare_irr > preferences.max_fare_irr:
        return False
    return bookable_class(offer, preferences) is not None


def eligible_offers(
    preferences: TripPreferences,
    offers: Iterable[ServiceOffer],
) -> tuple[ServiceOffer, ...]:
    """Return the offers that satisfy ``preferences``, ordered by departure."""

    kept = [offer for offer in offers if is_eligible(offer, preferences)]
    kept.sort(key=lambda offer: (offer.service.departs_at, offer.service.service_id))
    return tuple(kept)


__all__ = ["bookable_class", "eligible_offers", "is_eligible"]
