"""Deterministic builders for provider and simulator tests.

Every timestamp is fixed and every identifier refers to a fixture service, so a
failing scenario can be reproduced from the test definition alone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from raja_assistant.adapters.simulator import SimulatedReservationProvider, SimulatorFaults
from raja_assistant.application.ports.models import LookupRequest, SubmissionRequest
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass
from raja_assistant.domain.models.reservation import ReservationIntent
from raja_assistant.domain.models.service import ServiceId
from support import NOW, make_preferences

ECONOMY = QuotaClass("economy")
BUSINESS = QuotaClass("business")
VIP = QuotaClass("vip")

#: Fully available on 2026-11-01, THR -> MHD.
MAIN_SERVICE = ServiceId("sim-1001")
#: 6 economy seats, business sold out, same route and date.
SECOND_SERVICE = ServiceId("sim-1002")
#: Economy sold out, business available, same route and date.
SOLD_OUT_ECONOMY_SERVICE = ServiceId("sim-1003")
#: 2026-11-02, THR -> MHD.
NEXT_DAY_SERVICE = ServiceId("sim-1101")
#: 2026-11-01, MHD -> THR.
RETURN_SERVICE = ServiceId("sim-1201")

DAY_ONE = date(2026, 11, 1)
DAY_TWO = date(2026, 11, 2)
QUOTE_TTL = timedelta(minutes=15)


def make_intent(
    *,
    intent_id: str = "intent-sim-0001",
    service_id: ServiceId = MAIN_SERVICE,
    quota_class: QuotaClass = ECONOMY,
    party_size: int = 2,
    fare_irr: int = 4_500_000,
    travel_date: date = DAY_ONE,
    quote_ttl: timedelta = QUOTE_TTL,
) -> ReservationIntent:
    """Build an intent that validates and refers to a fixture service."""

    return ReservationIntent(
        intent_id=intent_id,
        preferences=make_preferences(
            travel_date=travel_date,
            party_size=party_size,
            quota_classes=frozenset({quota_class}),
        ),
        service_id=service_id,
        quota_class=quota_class,
        passenger_refs=tuple(
            PassengerRef(f"pax-sim{index:02d}") for index in range(1, party_size + 1)
        ),
        fare_irr=fare_irr,
        quote_expires_at=NOW + quote_ttl,
        created_at=NOW,
    )


def make_submission(
    *,
    intent: ReservationIntent | None = None,
    attempt_epoch: int = 1,
    submitted_at: datetime = NOW,
) -> SubmissionRequest:
    """Build a submission whose idempotency key matches its intent."""

    return SubmissionRequest.for_intent(
        intent if intent is not None else make_intent(),
        submitted_at=submitted_at,
        attempt_epoch=attempt_epoch,
    )


def make_lookup(
    request: SubmissionRequest,
    *,
    requested_at: datetime = NOW,
) -> LookupRequest:
    """Build a lookup that provably refers to ``request``."""

    return LookupRequest.for_submission(request, requested_at=requested_at)


def make_provider(**faults: bool) -> SimulatedReservationProvider:
    """Return a simulator with exactly the named fault switches enabled."""

    return SimulatedReservationProvider(faults=SimulatorFaults(**faults))


__all__ = [
    "BUSINESS",
    "DAY_ONE",
    "DAY_TWO",
    "ECONOMY",
    "MAIN_SERVICE",
    "NEXT_DAY_SERVICE",
    "QUOTE_TTL",
    "RETURN_SERVICE",
    "SECOND_SERVICE",
    "SOLD_OUT_ECONOMY_SERVICE",
    "VIP",
    "make_intent",
    "make_lookup",
    "make_provider",
    "make_submission",
]
