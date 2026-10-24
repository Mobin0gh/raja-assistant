"""Domain value objects for stations, quota classes, and trip preferences."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time

from raja_assistant.domain.validation import require

_STATION_PATTERN = re.compile(r"[A-Z0-9]{2,10}")
_QUOTA_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")


@dataclass(frozen=True, slots=True, order=True)
class StationCode:
    """Opaque station identifier.

    The real station catalogue is an unresolved external dependency, so this
    value object only enforces a local, provider-neutral shape. It does not
    claim to match any Raja-specific code space.
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        require(
            bool(_STATION_PATTERN.fullmatch(normalized)),
            "station_code",
            "must be 2-10 characters of A-Z or 0-9",
        )
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True, order=True)
class QuotaClass:
    """Opaque fare/quota class label, normalized to lowercase.

    Local labels only: the provider's real quota classes are unknown until the
    authorized contract is available.
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        require(
            bool(_QUOTA_PATTERN.fullmatch(normalized)),
            "quota_class",
            "must be 1-32 characters of a-z, 0-9, '_' or '-'",
        )
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class TripPreferences:
    """What the traveller wants, independent of any provider.

    ``earliest_departure`` and ``latest_departure`` are wall-clock times on the
    travel date, not aware datetimes: the offset is applied when a concrete
    service is evaluated.
    """

    origin: StationCode
    destination: StationCode
    travel_date: date
    party_size: int
    quota_classes: frozenset[QuotaClass] = field(default_factory=frozenset)
    max_fare_irr: int | None = None
    earliest_departure: time | None = None
    latest_departure: time | None = None

    def __post_init__(self) -> None:
        require(self.origin != self.destination, "destination", "must differ from origin")
        require(1 <= self.party_size <= 10, "party_size", "must be between 1 and 10")
        if self.max_fare_irr is not None:
            require(self.max_fare_irr > 0, "max_fare_irr", "must be positive when set")
        if self.earliest_departure is not None and self.latest_departure is not None:
            require(
                self.earliest_departure <= self.latest_departure,
                "departure_window",
                "earliest must not be later than latest",
            )
