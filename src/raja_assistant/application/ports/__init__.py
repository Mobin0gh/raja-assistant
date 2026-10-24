"""Provider ports: the contracts adapters implement and the application consumes."""

from raja_assistant.application.ports.errors import (
    CapabilityUnsupported,
    DuplicateSubmissionAttempt,
    ProviderContractError,
    ProviderError,
    ProviderNotAuthorized,
)
from raja_assistant.application.ports.models import (
    LookupRequest,
    LookupResult,
    ProviderOutcome,
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationPending,
    ReservationRejected,
    SearchResult,
    SearchSucceeded,
    ServiceOffer,
    SubmissionOutcomeUnknown,
    SubmissionRequest,
    SubmissionResult,
)
from raja_assistant.application.ports.provider import (
    ReservationProvider,
    require_booking_capability,
)

__all__ = [
    "CapabilityUnsupported",
    "DuplicateSubmissionAttempt",
    "LookupRequest",
    "LookupResult",
    "ProviderContractError",
    "ProviderError",
    "ProviderNotAuthorized",
    "ProviderOutcome",
    "ProviderUnavailable",
    "ReservationConfirmed",
    "ReservationHeld",
    "ReservationNotFound",
    "ReservationPending",
    "ReservationProvider",
    "ReservationRejected",
    "SearchResult",
    "SearchSucceeded",
    "ServiceOffer",
    "SubmissionOutcomeUnknown",
    "SubmissionRequest",
    "SubmissionResult",
    "require_booking_capability",
]
