"""Train services and their availability, as observed from a provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from raja_assistant.domain.models.preferences import QuotaClass, StationCode
from raja_assistant.domain.validation import require, require_aware

_SERVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


@dataclass(frozen=True, slots=True, order=True)
class ServiceId:
    """Opaque provider-neutral identifier for one dated train service."""

    value: str

    def __post_init__(self) -> None:
        require(isinstance(self.value, str), "service_id", "must be a string")
        require(
            bool(_SERVICE_ID_PATTERN.fullmatch(self.value)),
            "service_id",
            "must match [A-Za-z0-9._:-]{1,64}",
        )


@dataclass(frozen=True, slots=True)
class TrainService:
    """One bookable departure returned by a provider adapter."""

    service_id: ServiceId
    train_name: str
    origin: StationCode
    destination: StationCode
    travel_date: date
    departs_at: datetime
    arrives_at: datetime
    fare_irr: int
    quota_classes: frozenset[QuotaClass]

    def __post_init__(self) -> None:
        require(self.origin != self.destination, "destination", "must differ from origin")
        require(1 <= len(self.train_name.strip()) <= 64, "train_name", "must be 1-64 characters")
        require_aware(self.departs_at, "departs_at")
        require_aware(self.arrives_at, "arrives_at")
        require(self.arrives_at > self.departs_at, "arrives_at", "must be after departs_at")
        require(
            self.departs_at.date() == self.travel_date,
            "travel_date",
            "must equal the departure date",
        )
        require(self.fare_irr > 0, "fare_irr", "must be positive")
        require(bool(self.quota_classes), "quota_classes", "must not be empty")


class AvailabilityStatus(StrEnum):
    """Three-valued availability: enough seats, none, or not observed."""

    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Availability:
    """A point-in-time observation, never a promise that a seat will exist."""

    service_id: ServiceId
    quota_class: QuotaClass
    status: AvailabilityStatus
    observed_at: datetime
    remaining_seats: int | None = None

    def __post_init__(self) -> None:
        require_aware(self.observed_at, "observed_at")
        if self.status is AvailabilityStatus.AVAILABLE:
            require(
                self.remaining_seats is not None and self.remaining_seats > 0,
                "remaining_seats",
                "must be positive when status is available",
            )
        elif self.status is AvailabilityStatus.SOLD_OUT:
            require(
                self.remaining_seats == 0,
                "remaining_seats",
                "must be 0 when status is sold_out",
            )
        else:
            require(
                self.remaining_seats is None,
                "remaining_seats",
                "must be None when status is unknown",
            )

    @property
    def is_bookable(self) -> bool:
        """Return True only for an explicit positive observation."""

        return self.status is AvailabilityStatus.AVAILABLE
