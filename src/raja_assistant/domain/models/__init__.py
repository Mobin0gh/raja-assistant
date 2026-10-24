"""Domain models: value objects and the reservation aggregate."""

from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, StationCode, TripPreferences
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.models.service import (
    Availability,
    AvailabilityStatus,
    ServiceId,
    TrainService,
)

__all__ = [
    "Availability",
    "AvailabilityStatus",
    "PassengerRef",
    "QuotaClass",
    "Reservation",
    "ReservationIntent",
    "ServiceId",
    "StationCode",
    "TrainService",
    "TripPreferences",
]
