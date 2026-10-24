"""The provider port: the only way the application reaches a booking system.

The protocol is deliberately tiny and transport-agnostic. An adapter may be a
local simulator, a documented partner interface, or nothing at all; the
application only sees these three operations and a capability declaration.

Transport note: the operations are synchronous because the contract describes
*decisions*, not I/O, and because a synchronous port keeps deterministic tests
free of an event loop. An adapter that performs real network I/O must run those
calls off the orchestrator's event loop (for example via ``asyncio.to_thread``);
whether the port itself becomes async is an open decision for the orchestration
phase, and is the one part of this contract expected to change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from raja_assistant.application.ports.errors import CapabilityUnsupported, ProviderNotAuthorized
from raja_assistant.application.ports.models import (
    LookupRequest,
    LookupResult,
    SearchResult,
    SubmissionRequest,
    SubmissionResult,
)
from raja_assistant.domain.models.preferences import TripPreferences
from raja_assistant.domain.reservation.guards import ProviderCapabilities


@runtime_checkable
class ReservationProvider(Protocol):
    """Minimum operations needed to search, submit, and reconcile.

    Implementations must be safe to call with the same request twice: a repeated
    submission carrying the same idempotency key returns the first outcome and
    never creates a second reservation.

    Outcome obligations every adapter must honour
    ---------------------------------------------

    The orchestrator cannot inspect the wire, so these distinctions are the
    adapter's responsibility. Reporting them wrongly is a safety defect, not a
    cosmetic one:

    ``ProviderUnavailable``
        Returned **only** when the adapter can guarantee the request was not
        processed, so nothing was booked. A pre-send connection failure or a
        rate-limit refusal before any request was accepted belongs here.

    ``SubmissionOutcomeUnknown``
        Returned whenever the request may have been processed but its answer
        cannot be trusted: a post-send timeout, a connection reset, or a lost
        acknowledgement. The orchestrator treats this as ambiguous and only ever
        resolves it with a lookup; it never re-submits.

    ``ReservationRejected``
        Returned **only** when the adapter can establish that no reservation was
        made. A refusal whose effect is unclear is an unknown outcome, not a
        rejection.

    No timeout or transport error may be reclassified to make the orchestrator's
    job easier; the mapping from transport events to these three results is part
    of what an adapter is approved to do.
    """

    @property
    def name(self) -> str:
        """Short, stable identifier used in logs and error messages."""

        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this adapter is allowed and able to do."""

        ...

    def search(self, preferences: TripPreferences, *, now: datetime) -> SearchResult:
        """Return the services the provider offers for validated preferences.

        ``now`` is passed in rather than read from the clock so that the same call
        with the same inputs is reproducible.
        """

        ...

    def submit(self, request: SubmissionRequest) -> SubmissionResult:
        """Submit one attempt, identified by its idempotency key.

        Must raise :class:`~raja_assistant.application.ports.errors.DuplicateSubmissionAttempt`
        rather than open a parallel reservation when the intent already has a live
        one. Must return ``SubmissionOutcomeUnknown`` for any post-send ambiguity
        and may return ``ProviderUnavailable`` only when no side effect occurred.
        """

        ...

    def lookup(self, request: LookupRequest) -> LookupResult:
        """Report what the provider knows about one previously submitted attempt."""

        ...


def require_booking_capability(provider: ReservationProvider) -> None:
    """Refuse to submit through an adapter that is not authorized to book.

    This exists so that a future real-provider adapter can be shipped disabled:
    it declares ``authorized=False`` and every submission path stops here before
    any request is attempted.
    """

    capabilities = provider.capabilities
    if not capabilities.authorized:
        raise ProviderNotAuthorized(provider.name)
    if not capabilities.book:
        raise CapabilityUnsupported(provider.name, "book")


__all__ = ["ReservationProvider", "require_booking_capability"]
