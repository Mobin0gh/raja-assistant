"""A deterministic, in-memory reservation provider.

The simulator exists so the reservation lifecycle can be exercised end to end
without contacting Raja or any other booking system. It:

* opens no sockets and reads no clock — every timestamp comes from the caller;
* starts from a fixed catalogue and copies it, so instances never share state;
* generates references that are obviously fake (``SIM-`` plus a key digest);
* never accepts payment details and never claims a real train booking.

Determinism is per call sequence: submitting consumes seats, so the state after
a sequence is a function of that sequence. Two fresh simulators given the same
calls always agree.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta

from raja_assistant.adapters.simulator.faults import SimulatorFaults
from raja_assistant.adapters.simulator.fixtures import BUILT_IN_CATALOGUE, SimulatedService
from raja_assistant.adapters.simulator.records import (
    SimulatedReservation,
    SimulatedReservationStatus,
)
from raja_assistant.application.ports.errors import (
    DuplicateSubmissionAttempt,
    ProviderContractError,
)
from raja_assistant.application.ports.models import (
    LookupRequest,
    LookupResult,
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
from raja_assistant.application.ports.provider import require_booking_capability
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences
from raja_assistant.domain.models.service import (
    Availability,
    AvailabilityStatus,
    ServiceId,
)
from raja_assistant.domain.reservation.guards import ProviderCapabilities
from raja_assistant.domain.validation import require, require_aware

PROVIDER_NAME = "simulator"
REFERENCE_PREFIX = "SIM"

#: How long a simulated hold lasts. Fixed so that tests are reproducible.
HOLD_TTL = timedelta(minutes=30)

#: The simulator is authorized *as a simulator*. ``pay`` stays False because no
#: payment path exists anywhere in this project.
SIMULATED_CAPABILITIES = ProviderCapabilities(
    authorized=True,
    book=True,
    hold=True,
    pay=False,
    lookup_by_reference=True,
)

def _reference_for(idempotency_key: str) -> str:
    """Return a stable, obviously fake provider reference for one submission."""

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:10].upper()
    return f"{REFERENCE_PREFIX}-{digest}"


class SimulatedReservationProvider:
    """An in-memory :class:`~raja_assistant.application.ports.provider.ReservationProvider`.

    Not thread safe by design: the orchestrator calls it from one place, and
    adding locks would hide ordering problems that tests should catch.
    """

    def __init__(
        self,
        *,
        faults: SimulatorFaults | None = None,
        catalogue: Sequence[SimulatedService] | None = None,
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        self._faults = faults if faults is not None else SimulatorFaults()
        source = tuple(catalogue) if catalogue is not None else BUILT_IN_CATALOGUE
        identifiers = [entry.service.service_id for entry in source]
        require(
            len(set(identifiers)) == len(identifiers),
            "catalogue",
            "must not repeat a service id",
        )
        self._catalogue = source
        self._by_service_id = {entry.service.service_id: entry for entry in source}
        self._seats: dict[ServiceId, dict[QuotaClass, int]] = {
            entry.service.service_id: dict(entry.seats) for entry in source
        }
        self._records: dict[str, SimulatedReservation] = {}
        self._answers: dict[str, SubmissionResult] = {}
        self._key_by_intent: dict[str, str] = {}
        self._capabilities = capabilities if capabilities is not None else SIMULATED_CAPABILITIES

    # ------------------------------------------------------------------ #
    # Port surface
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def search(self, preferences: TripPreferences, *, now: datetime) -> SearchResult:
        """Return matching fixture services with their current availability."""

        require_aware(now, "now")
        if self._faults.search_unavailable:
            return ProviderUnavailable(observed_at=now, detail="simulated search outage")

        wanted = preferences.quota_classes
        offers: list[ServiceOffer] = []
        for entry in self._catalogue:
            service = entry.service
            if service.origin != preferences.origin:
                continue
            if service.destination != preferences.destination:
                continue
            if service.travel_date != preferences.travel_date:
                continue
            classes = [
                name for name in sorted(service.quota_classes) if not wanted or name in wanted
            ]
            if not classes:
                continue
            offers.append(
                ServiceOffer(
                    service=service,
                    availability=tuple(
                        self._observe(service.service_id, quota_class, now)
                        for quota_class in classes
                    ),
                )
            )

        offers.sort(key=lambda offer: (offer.service.departs_at, offer.service.service_id.value))
        return SearchSucceeded(offers=tuple(offers), observed_at=now)

    def submit(self, request: SubmissionRequest) -> SubmissionResult:
        """Submit one attempt, honouring the idempotency key exactly."""

        require_booking_capability(self)

        # The domain derives the key from the whole request identity, so a stored
        # key can only ever refer to an equivalent request. Returning the first
        # answer verbatim is therefore both safe and exactly idempotent.
        replayed = self._answers.get(request.idempotency_key)
        if replayed is not None:
            return replayed

        live_key = self._key_by_intent.get(request.intent.intent_id)
        if live_key is not None:
            existing = self._records[live_key]
            raise DuplicateSubmissionAttempt(request.intent.intent_id, existing.provider_reference)

        fault_result = self._submit_fault_result(request)
        if fault_result is not None:
            return fault_result

        entry = self._by_service_id.get(request.intent.service_id)
        if entry is None:
            return self._reject("unknown_service", request.submitted_at)

        quota_class = request.intent.quota_class
        seats = self._seats[entry.service.service_id]
        if quota_class not in seats:
            return self._reject("unsupported_quota_class", request.submitted_at)

        party_size = len(request.intent.passenger_refs)
        if seats[quota_class] < party_size:
            return self._reject("sold_out", request.submitted_at)

        status = self._status_for_request()
        hold_expires_at = (
            request.submitted_at + HOLD_TTL
            if status is SimulatedReservationStatus.HELD
            else None
        )
        record = SimulatedReservation(
            idempotency_key=request.idempotency_key,
            intent_id=request.intent.intent_id,
            service_id=request.intent.service_id,
            quota_class=quota_class,
            party_size=party_size,
            attempt_epoch=request.attempt_epoch,
            status=status,
            provider_reference=_reference_for(request.idempotency_key),
            accepted_at=request.submitted_at,
            hold_expires_at=hold_expires_at,
        )
        seats[quota_class] -= party_size
        self._records[record.idempotency_key] = record
        self._key_by_intent[record.intent_id] = record.idempotency_key

        answer = self._answer_for(record)
        self._answers[record.idempotency_key] = answer
        return answer

    def lookup(self, request: LookupRequest) -> LookupResult:
        """Report what the simulator knows about one previously submitted attempt."""

        require_aware(request.requested_at, "requested_at")
        if self._faults.lookup_unavailable:
            return ProviderUnavailable(
                observed_at=request.requested_at,
                detail="simulated lookup outage",
            )

        record = self._records.get(request.idempotency_key)
        if record is not None and record.intent_id != request.intent_id:
            raise ProviderContractError(
                "intent_id",
                "does not match the recorded submission for this idempotency key",
            )
        if record is None or self._faults.lookup_reports_not_found:
            return ReservationNotFound(
                client_reference=request.idempotency_key,
                observed_at=request.requested_at,
            )
        return self._answer_for(record, observed_at=request.requested_at)

    # ------------------------------------------------------------------ #
    # Read-only inspection, for tests and diagnostics
    # ------------------------------------------------------------------ #

    @property
    def records(self) -> tuple[SimulatedReservation, ...]:
        """Return every stored record, in insertion order."""

        return tuple(self._records.values())

    def record_for(self, idempotency_key: str) -> SimulatedReservation | None:
        """Return the record stored for ``idempotency_key``, if any."""

        return self._records.get(idempotency_key)

    def remaining_seats(self, service_id: ServiceId, quota_class: QuotaClass) -> int | None:
        """Return the currently available seats, or None for an unknown service/class."""

        seats = self._seats.get(service_id)
        if seats is None:
            return None
        return seats.get(quota_class)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _observe(
        self,
        service_id: ServiceId,
        quota_class: QuotaClass,
        now: datetime,
    ) -> Availability:
        remaining = self._seats[service_id][quota_class]
        status = AvailabilityStatus.AVAILABLE if remaining > 0 else AvailabilityStatus.SOLD_OUT
        return Availability(
            service_id=service_id,
            quota_class=quota_class,
            status=status,
            observed_at=now,
            remaining_seats=remaining,
        )

    def _submit_fault_result(self, request: SubmissionRequest) -> SubmissionResult | None:
        faults = self._faults
        if faults.submit_unavailable:
            return ProviderUnavailable(
                observed_at=request.submitted_at,
                detail="simulated submission outage",
            )
        if faults.submit_rejected:
            return self._reject("simulated_rejection", request.submitted_at)
        return None

    def _status_for_request(self) -> SimulatedReservationStatus:
        if self._faults.submit_pending:
            return SimulatedReservationStatus.PENDING
        if self._faults.submit_holds_seats:
            return SimulatedReservationStatus.HELD
        return SimulatedReservationStatus.CONFIRMED

    def _answer_for(
        self,
        record: SimulatedReservation,
        *,
        observed_at: datetime | None = None,
    ) -> ReservationConfirmed | ReservationHeld | ReservationPending | SubmissionOutcomeUnknown:
        """Translate a stored record into the answer the caller receives."""

        moment = record.accepted_at if observed_at is None else observed_at
        if record.status is SimulatedReservationStatus.CONFIRMED:
            if self._faults.submit_outcome_unknown and observed_at is None:
                return SubmissionOutcomeUnknown(
                    client_reference=record.idempotency_key,
                    observed_at=moment,
                    detail="simulated acknowledgement loss",
                )
            return ReservationConfirmed(
                provider_reference=record.provider_reference,
                observed_at=moment,
            )
        if record.status is SimulatedReservationStatus.HELD:
            hold_expires_at = record.hold_expires_at
            if hold_expires_at is None:  # pragma: no cover - refused at construction
                raise ProviderContractError("hold_expires_at", "a held record must carry an expiry")
            return ReservationHeld(
                provider_reference=record.provider_reference,
                hold_expires_at=hold_expires_at,
                observed_at=moment,
            )
        return ReservationPending(
            client_reference=record.idempotency_key,
            observed_at=moment,
        )

    @staticmethod
    def _reject(reason: str, observed_at: datetime) -> ReservationRejected:
        return ReservationRejected(reason=reason, observed_at=observed_at)


__all__ = [
    "HOLD_TTL",
    "PROVIDER_NAME",
    "REFERENCE_PREFIX",
    "SIMULATED_CAPABILITIES",
    "SimulatedReservationProvider",
]
