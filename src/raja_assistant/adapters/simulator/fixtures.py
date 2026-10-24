"""Fixed, obviously fake data for the simulator.

Every service id starts with ``sim-`` and every provider reference starts with
``SIM-``, so a simulator artefact can never be mistaken for a real booking. The
timetables, fares, and seat counts below are invented. They describe no real
train and no real Raja offering.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

from raja_assistant.domain.models.preferences import QuotaClass, StationCode
from raja_assistant.domain.models.service import ServiceId, TrainService
from raja_assistant.domain.validation import require

#: Iran has observed a fixed +03:30 offset since 2022. This is a fixture detail,
#: not a claim about how a real provider encodes time.
SIMULATED_TIMEZONE = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

ECONOMY = QuotaClass("economy")
BUSINESS = QuotaClass("business")

TEHRAN = StationCode("THR")
MASHHAD = StationCode("MHD")

_DAY_ONE = date(2026, 11, 1)
_DAY_TWO = date(2026, 11, 2)
_DAY_THREE = date(2026, 11, 3)


@dataclass(frozen=True, slots=True)
class SimulatedService:
    """A fixture service and the seats currently available on it."""

    service: TrainService
    seats: Mapping[QuotaClass, int]

    def __post_init__(self) -> None:
        require(bool(self.seats), "seats", "must not be empty")
        require(
            set(self.seats) == self.service.quota_classes,
            "seats",
            "must describe exactly the quota classes the service offers",
        )
        for quota_class, count in self.seats.items():
            require(count >= 0, f"seats[{quota_class.value}]", "must not be negative")


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SIMULATED_TIMEZONE)


def _service(
    *,
    service_id: str,
    train_name: str,
    origin: StationCode,
    destination: StationCode,
    travel_date: date,
    departs_at: datetime,
    arrives_at: datetime,
    fare_irr: int,
    classes: Mapping[QuotaClass, int],
) -> SimulatedService:
    return SimulatedService(
        service=TrainService(
            service_id=ServiceId(service_id),
            train_name=train_name,
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            departs_at=departs_at,
            arrives_at=arrives_at,
            fare_irr=fare_irr,
            quota_classes=frozenset(classes),
        ),
        seats=MappingProxyType(dict(classes)),
    )


def _build_catalogue() -> tuple[SimulatedService, ...]:
    """Build the fixed catalogue.

    Deliberately covers the cases the contract must survive: a fully available
    service, a service whose business class is sold out, a service whose economy
    class is sold out, a second travel date, and the reverse route.
    """

    return (
        _service(
            service_id="sim-1001",
            train_name="Simulated Express 1001",
            origin=TEHRAN,
            destination=MASHHAD,
            travel_date=_DAY_ONE,
            departs_at=_at(_DAY_ONE, 20, 30),
            arrives_at=_at(_DAY_TWO, 6, 15),
            fare_irr=4_500_000,
            classes={ECONOMY: 12, BUSINESS: 4},
        ),
        _service(
            service_id="sim-1002",
            train_name="Simulated Sleeper 1002",
            origin=TEHRAN,
            destination=MASHHAD,
            travel_date=_DAY_ONE,
            departs_at=_at(_DAY_ONE, 22, 15),
            arrives_at=_at(_DAY_TWO, 7, 40),
            fare_irr=3_900_000,
            classes={ECONOMY: 6, BUSINESS: 0},
        ),
        _service(
            service_id="sim-1003",
            train_name="Simulated Local 1003",
            origin=TEHRAN,
            destination=MASHHAD,
            travel_date=_DAY_ONE,
            departs_at=_at(_DAY_ONE, 18, 45),
            arrives_at=_at(_DAY_TWO, 5, 10),
            fare_irr=5_200_000,
            classes={ECONOMY: 0, BUSINESS: 2},
        ),
        _service(
            service_id="sim-1101",
            train_name="Simulated Express 1101",
            origin=TEHRAN,
            destination=MASHHAD,
            travel_date=_DAY_TWO,
            departs_at=_at(_DAY_TWO, 21, 0),
            arrives_at=_at(_DAY_THREE, 6, 30),
            fare_irr=4_100_000,
            classes={ECONOMY: 8, BUSINESS: 2},
        ),
        _service(
            service_id="sim-1201",
            train_name="Simulated Express 1201",
            origin=MASHHAD,
            destination=TEHRAN,
            travel_date=_DAY_ONE,
            departs_at=_at(_DAY_ONE, 21, 30),
            arrives_at=_at(_DAY_TWO, 6, 45),
            fare_irr=4_200_000,
            classes={ECONOMY: 5, BUSINESS: 0},
        ),
    )


#: The catalogue a fresh simulator starts from. Immutable, so two simulators
#: constructed in the same test never share mutable state.
BUILT_IN_CATALOGUE: tuple[SimulatedService, ...] = _build_catalogue()

__all__ = [
    "BUILT_IN_CATALOGUE",
    "BUSINESS",
    "ECONOMY",
    "MASHHAD",
    "SIMULATED_TIMEZONE",
    "TEHRAN",
    "SimulatedService",
]
