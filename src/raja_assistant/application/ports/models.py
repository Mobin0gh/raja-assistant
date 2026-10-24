"""Typed requests and results exchanged with a reservation provider.

Design rules that the rest of the system depends on:

* **Outcomes are values.** A rejection, a hold, a pending answer, an unavailable
  provider, and an ambiguous submission are returned as results, never raised as
  exceptions. The caller is therefore forced to look at the outcome.
* **A confirmation always carries a provider reference.** There is no result type
  that says "probably booked".
* **An unknown outcome is its own result.** It is the only result that means "the
  provider may or may not have acted", and it can only be resolved by a lookup
  with the same idempotency key.
* **Shape errors** (a naive timestamp, a blank reference) raise
  :class:`~raja_assistant.domain.errors.ValidationError`, the same validator the
  domain uses. **Contract misuse** (a key that does not match its request, a
  request the adapter cannot honour) raises
  :class:`~raja_assistant.application.ports.errors.ProviderContractError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from raja_assistant.application.ports.errors import ProviderContractError
from raja_assistant.domain.models.preferences import QuotaClass
from raja_assistant.domain.models.reservation import ReservationIntent
from raja_assistant.domain.models.service import Availability, TrainService
from raja_assistant.domain.reservation.idempotency import KEY_PREFIX, derive_idempotency_key
from raja_assistant.domain.validation import require_aware

MAX_REFERENCE_LENGTH = 120
MAX_DETAIL_LENGTH = 200


def _check(condition: bool, field: str, message: str) -> None:
    """Refuse a contract violation with a stable ``field`` name."""

    if not condition:
        raise ProviderContractError(field, message)


def _check_text(value: str, field: str, max_length: int = MAX_REFERENCE_LENGTH) -> None:
    _check(isinstance(value, str), field, "must be a string")
    _check(bool(value.strip()), field, "must not be blank")
    _check(len(value) <= max_length, field, f"must be at most {max_length} characters")


def _check_optional_text(
    value: str | None,
    field: str,
    max_length: int = MAX_DETAIL_LENGTH,
) -> None:
    if value is not None:
        _check_text(value, field, max_length)


def _check_idempotency_key(value: str) -> None:
    _check_text(value, "idempotency_key", max_length=200)
    _check(
        value.startswith(f"{KEY_PREFIX}:"),
        "idempotency_key",
        "must be produced by the domain key derivation",
    )


class ProviderOutcome(StrEnum):
    """The complete vocabulary of what one provider call can mean.

    Every result class below exposes one of these as a class-level ``outcome``,
    so callers can dispatch on the value without relying on class identity.
    """

    FOUND = "found"
    CONFIRMED = "confirmed"
    HELD = "held"
    PENDING = "pending"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    OUTCOME_UNKNOWN = "outcome_unknown"
    UNAVAILABLE = "unavailable"


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    """One attempt to turn an approved intent into a provider reservation.

    The request carries the domain's own idempotency key and refuses to be built
    with a key that does not match its intent and attempt epoch. That single
    check is what makes a replayed submission reuse its first outcome instead of
    creating a second booking.
    """

    idempotency_key: str
    intent: ReservationIntent
    attempt_epoch: int
    submitted_at: datetime

    def __post_init__(self) -> None:
        _check_idempotency_key(self.idempotency_key)
        _check(
            isinstance(self.intent, ReservationIntent),
            "intent",
            "must be a ReservationIntent",
        )
        # The attempt epoch is validated by the key derivation below, which is the
        # single authority on what a valid attempt is.
        require_aware(self.submitted_at, "submitted_at")

        expected = derive_idempotency_key(
            intent_id=self.intent.intent_id,
            service_id=self.intent.service_id,
            travel_date=self.intent.preferences.travel_date,
            passenger_refs=self.intent.passenger_refs,
            quota_class=self.intent.quota_class,
            attempt_epoch=self.attempt_epoch,
        )
        _check(
            self.idempotency_key == expected,
            "idempotency_key",
            "must be derived from this intent and attempt epoch",
        )

    @classmethod
    def for_intent(
        cls,
        intent: ReservationIntent,
        *,
        submitted_at: datetime,
        attempt_epoch: int = 1,
    ) -> SubmissionRequest:
        """Build a request whose key is derived from ``intent`` and ``attempt_epoch``."""

        key = derive_idempotency_key(
            intent_id=intent.intent_id,
            service_id=intent.service_id,
            travel_date=intent.preferences.travel_date,
            passenger_refs=intent.passenger_refs,
            quota_class=intent.quota_class,
            attempt_epoch=attempt_epoch,
        )
        return cls(
            idempotency_key=key,
            intent=intent,
            attempt_epoch=attempt_epoch,
            submitted_at=submitted_at,
        )


@dataclass(frozen=True, slots=True)
class LookupRequest:
    """A reconciliation query for one previously submitted attempt.

    Lookup is keyed by the *client's* identity for the submission, never by a
    provider reference: after an ambiguous submission the caller may not have a
    reference yet, and guessing one is how applications book twice.
    """

    idempotency_key: str
    intent_id: str
    requested_at: datetime

    def __post_init__(self) -> None:
        _check_idempotency_key(self.idempotency_key)
        _check_text(self.intent_id, "intent_id", max_length=64)
        require_aware(self.requested_at, "requested_at")

    @classmethod
    def for_submission(
        cls,
        request: SubmissionRequest,
        *,
        requested_at: datetime,
    ) -> LookupRequest:
        """Build a lookup that provably refers to the same submission."""

        return cls(
            idempotency_key=request.idempotency_key,
            intent_id=request.intent.intent_id,
            requested_at=requested_at,
        )


# --------------------------------------------------------------------------- #
# Search results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ServiceOffer:
    """One service together with the availability observed for it.

    Availability is reported per quota class and is a point-in-time observation,
    never a promise. Fare and departure-window matching are traveller policy, not
    provider behaviour, so they are not applied here.
    """

    service: TrainService
    availability: tuple[Availability, ...]

    def __post_init__(self) -> None:
        _check(bool(self.availability), "availability", "must not be empty")
        _check(
            {item.service_id for item in self.availability} == {self.service.service_id},
            "availability",
            "must all describe the offered service",
        )
        classes = [item.quota_class for item in self.availability]
        _check(len(set(classes)) == len(classes), "availability", "must not repeat a quota class")

    @property
    def quota_classes(self) -> tuple[QuotaClass, ...]:
        """Return the observed quota classes in a stable order."""

        return tuple(sorted(item.quota_class for item in self.availability))

    def availability_for(self, quota_class: QuotaClass) -> Availability | None:
        """Return the observation for ``quota_class``, or None when not offered."""

        for item in self.availability:
            if item.quota_class == quota_class:
                return item
        return None

    @property
    def is_bookable(self) -> bool:
        """Return True only when at least one class is explicitly available."""

        return any(item.is_bookable for item in self.availability)


@dataclass(frozen=True, slots=True)
class SearchSucceeded:
    """The provider answered; ``offers`` may legitimately be empty."""

    offers: tuple[ServiceOffer, ...]
    observed_at: datetime
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.FOUND

    def __post_init__(self) -> None:
        require_aware(self.observed_at, "observed_at")


# --------------------------------------------------------------------------- #
# Submission and lookup results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReservationConfirmed:
    """The provider states that a reservation exists, identified by ``provider_reference``."""

    provider_reference: str
    observed_at: datetime
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.CONFIRMED

    def __post_init__(self) -> None:
        _check_text(self.provider_reference, "provider_reference")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class ReservationHeld:
    """Seats are held but not yet a reservation; the hold expires on its own."""

    provider_reference: str
    hold_expires_at: datetime
    observed_at: datetime
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.HELD

    def __post_init__(self) -> None:
        _check_text(self.provider_reference, "provider_reference")
        require_aware(self.hold_expires_at, "hold_expires_at")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class ReservationPending:
    """The provider accepted the request but has not decided yet."""

    client_reference: str
    observed_at: datetime
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.PENDING

    def __post_init__(self) -> None:
        _check_text(self.client_reference, "client_reference")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class ReservationRejected:
    """A definitive refusal the adapter has established: nothing was booked.

    Only an adapter that can confirm no reservation was made may return this. An
    answer whose effect is unclear is ``SubmissionOutcomeUnknown``, never a
    rejection; reading it as one is how a real booking gets forgotten.
    """

    reason: str
    observed_at: datetime
    detail: str | None = None
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.REJECTED

    def __post_init__(self) -> None:
        _check_text(self.reason, "reason", max_length=64)
        _check_optional_text(self.detail, "detail")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class ReservationNotFound:
    """Lookup answered definitively: no reservation exists for this client reference."""

    client_reference: str
    observed_at: datetime
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.NOT_FOUND

    def __post_init__(self) -> None:
        _check_text(self.client_reference, "client_reference")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class SubmissionOutcomeUnknown:
    """The provider may or may not have acted, and its answer cannot be trusted.

    This is the result for a post-send timeout, a connection reset, or a lost
    acknowledgement: cases where the request may have been processed. It is the
    most dangerous result in the system and never licenses a retry — the only
    permitted next step is a lookup with the same ``client_reference``.
    """

    client_reference: str
    observed_at: datetime
    detail: str | None = None
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.OUTCOME_UNKNOWN

    def __post_init__(self) -> None:
        _check_text(self.client_reference, "client_reference")
        _check_optional_text(self.detail, "detail")
        require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class ProviderUnavailable:
    """A temporary condition: the provider could not serve the call at all.

    An adapter may return this only when it can guarantee the request was **not**
    processed (no side effect). A post-send timeout or a lost acknowledgement is
    ``SubmissionOutcomeUnknown`` instead. Nothing is known about the underlying
    reservation, so this result must never be read as "rejected" or as "not
    booked".
    """

    observed_at: datetime
    detail: str | None = None
    outcome: ClassVar[ProviderOutcome] = ProviderOutcome.UNAVAILABLE

    def __post_init__(self) -> None:
        _check_optional_text(self.detail, "detail")
        require_aware(self.observed_at, "observed_at")


SearchResult = SearchSucceeded | ProviderUnavailable

SubmissionResult = (
    ReservationConfirmed
    | ReservationHeld
    | ReservationPending
    | ReservationRejected
    | SubmissionOutcomeUnknown
    | ProviderUnavailable
)

LookupResult = (
    ReservationConfirmed
    | ReservationHeld
    | ReservationPending
    | ReservationNotFound
    | ProviderUnavailable
)

__all__ = [
    "LookupRequest",
    "LookupResult",
    "ProviderOutcome",
    "ProviderUnavailable",
    "ReservationConfirmed",
    "ReservationHeld",
    "ReservationNotFound",
    "ReservationPending",
    "ReservationRejected",
    "SearchResult",
    "SearchSucceeded",
    "ServiceOffer",
    "SubmissionOutcomeUnknown",
    "SubmissionRequest",
    "SubmissionResult",
]
