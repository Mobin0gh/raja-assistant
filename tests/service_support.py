"""Builders and failure doubles for the orchestration tests.

Every timestamp is fixed, every identifier refers to a fixture service, and every
failure is injected explicitly. Nothing here sleeps, retries, or reaches the
network (``tests/conftest.py`` blocks sockets for the whole suite).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from provider_support import ECONOMY, MAIN_SERVICE, QUOTE_TTL, make_intent, make_provider
from raja_assistant.adapters.simulator import (
    BUILT_IN_CATALOGUE,
    SimulatedReservationProvider,
    SimulatedService,
    SimulatorFaults,
)
from raja_assistant.application.conflicts import InMemoryConflictLedger, Quarantine
from raja_assistant.application.models import ReservationRecord, ReservationView
from raja_assistant.application.ports.models import (
    SearchSucceeded,
    ServiceOffer,
)
from raja_assistant.application.service import ReservationService
from raja_assistant.application.store import InMemoryReservationStore, SequentialIdFactory
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.reservation.events import ReservationEvent
from raja_assistant.domain.reservation.guards import (
    Approval,
    ProviderCapabilities,
    ReservationContext,
)
from raja_assistant.domain.reservation.transitions import transition
from support import NOW, make_preferences

LATER = NOW + timedelta(minutes=5)
AFTER_QUOTE = NOW + QUOTE_TTL


def passenger_refs(party_size: int = 2) -> tuple[PassengerRef, ...]:
    """Return deterministic, non-identifying references for the party."""

    return tuple(PassengerRef(f"pax-sim{index:02d}") for index in range(1, party_size + 1))


class SpyProvider:
    """A simulator with call counting and scripted misbehaviour.

    ``raise_on_*`` raises before the underlying call; ``*_result`` overrides it.
    Both exist so tests can drive the exception and malformed-answer branches
    that no fault switch can produce.
    """

    def __init__(self, inner: SimulatedReservationProvider | None = None, *, events: list[str] | None = None):  # noqa: E501
        self.inner = inner if inner is not None else SimulatedReservationProvider()
        self.events = events if events is not None else []
        self.submit_calls = 0
        self.lookup_calls = 0
        self.search_calls = 0
        self.raise_on_submit: BaseException | None = None
        self.raise_on_lookup: BaseException | None = None
        self.submit_result: object | None = None
        self.lookup_result: object | None = None

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.inner.capabilities

    def search(self, preferences: TripPreferences, *, now: datetime) -> object:
        self.search_calls += 1
        self.events.append("search")
        return self.inner.search(preferences, now=now)

    def submit(self, request: object) -> object:
        self.submit_calls += 1
        self.events.append("submit")
        if self.raise_on_submit is not None:
            raise self.raise_on_submit
        if self.submit_result is not None:
            return self.submit_result
        return self.inner.submit(request)  # type: ignore[arg-type]

    def lookup(self, request: object) -> object:
        self.lookup_calls += 1
        self.events.append("lookup")
        if self.raise_on_lookup is not None:
            raise self.raise_on_lookup
        if self.lookup_result is not None:
            return self.lookup_result
        return self.inner.lookup(request)  # type: ignore[arg-type]


class RecordingStore:
    """An in-memory store that logs commits and can fail before writing.

    ``fail_on_save`` is the one-based number of the save that raises. The failure
    happens before any mutation, which is exactly the atomicity contract: a failed
    save leaves the previously stored record untouched.
    """

    def __init__(self, *, events: list[str] | None = None) -> None:
        self._inner = InMemoryReservationStore()
        self.events = events if events is not None else []
        self.save_calls = 0
        self.fail_on_save: int | None = None
        self.saved_ids: list[str] = []

    def get(self, reservation_id: str) -> ReservationRecord | None:
        return self._inner.get(reservation_id)

    def save(self, record: ReservationRecord) -> None:
        self.save_calls += 1
        if self.fail_on_save is not None and self.save_calls == self.fail_on_save:
            raise OSError("injected store failure")
        self._inner.save(record)
        self.events.append(f"save:{record.reservation.state.value}")
        self.saved_ids.append(record.reservation.reservation_id)

    def open_reservations(self) -> tuple[ReservationRecord, ...]:
        return self._inner.open_reservations()

    def open_for_intent(self, intent_id: str) -> tuple[ReservationRecord, ...]:
        return self._inner.open_for_intent(intent_id)

    def __len__(self) -> int:
        return len(self._inner)


class FailingLedger:
    """A ledger whose latch write always fails (the quarantine path)."""

    def __init__(self) -> None:
        self.latch_calls = 0

    def latch(self, reservation_id: str, conflict_reference: str) -> None:
        self.latch_calls += 1
        raise OSError("injected ledger failure")

    def reference_for(self, reservation_id: str) -> str | None:
        return None


@dataclass
class Harness:
    """A service wired to doubles a test can inspect."""

    service: ReservationService
    provider: SpyProvider
    store: RecordingStore
    ledger: InMemoryConflictLedger
    quarantine: Quarantine
    events: list[str]

    def quote(
        self,
        *,
        preferences: TripPreferences | None = None,
        quota_class: QuotaClass | None = None,
        now: datetime = NOW,
        quote_ttl: timedelta = QUOTE_TTL,
        fare_irr: int | None = None,
    ) -> ReservationView:
        """Open a quote for a fixture service and return its view."""

        preferences = preferences if preferences is not None else make_preferences()
        offer = offer_for(preferences, provider=self.provider, now=now)
        extra = {} if fare_irr is None else {"fare_irr": fare_irr}
        return self.service.open_quote(
            offer,
            quota_class=quota_class if quota_class is not None else ECONOMY,
            preferences=preferences,
            passenger_refs=passenger_refs(preferences.party_size),
            quote_expires_at=now + quote_ttl,
            now=now,
            **extra,
        )


def build_service(
    *,
    faults: SimulatorFaults | None = None,
    capabilities: ProviderCapabilities | None = None,
    events: list[str] | None = None,
) -> Harness:
    """Return a service whose store, ledger, and provider calls are observable."""

    resolved_events: list[str] = [] if events is None else events
    inner = make_provider() if faults is None and capabilities is None else SimulatedReservationProvider(  # noqa: E501
        faults=faults if faults is not None else SimulatorFaults(),
        capabilities=capabilities,
    )
    provider = SpyProvider(inner, events=resolved_events)
    store = RecordingStore(events=resolved_events)
    ledger = InMemoryConflictLedger()
    quarantine = Quarantine()
    service = ReservationService(
        provider=provider,
        store=store,
        ledger=ledger,
        quarantine=quarantine,
        ids=SequentialIdFactory(),
    )
    return Harness(
        service=service,
        provider=provider,
        store=store,
        ledger=ledger,
        quarantine=quarantine,
        events=resolved_events,
    )


def offer_for(
    preferences: TripPreferences,
    *,
    provider: SpyProvider | SimulatedReservationProvider | None = None,
    now: datetime = NOW,
    service_id: object = MAIN_SERVICE,
) -> ServiceOffer:
    """Return the fixture offer for ``service_id`` as the provider reported it."""

    source = provider if provider is not None else make_provider()
    result = source.search(preferences, now=now)
    assert isinstance(result, SearchSucceeded)
    for offer in result.offers:
        if offer.service.service_id == service_id:
            return offer
    raise AssertionError(f"fixture service {service_id} was not offered")


def fixture_service(service_id: object = MAIN_SERVICE) -> SimulatedService:
    """Return the raw fixture entry, for offer construction in policy tests."""

    for entry in BUILT_IN_CATALOGUE:
        if entry.service.service_id == service_id:
            return entry
    raise AssertionError(f"fixture service {service_id} does not exist")


def approval_for(view: ReservationView, *, now: datetime = NOW, approved_by: str = "operator") -> Approval:  # noqa: E501
    """Approve exactly the intent behind ``view``."""

    return Approval(
        intent_id=view.intent.intent_id,
        approved_by=approved_by,
        approved_at=now,
    )


def manual_quote(
    harness: Harness,
    intent: ReservationIntent,
    *,
    reservation_id: str = "rsv-manual-0001",
    now: datetime = NOW,
) -> ReservationView:
    """Insert a quote built directly through the real transition table.

    Used to reach states ``open_quote`` cannot produce for itself — most
    importantly a second reservation for an intent that is already live, which is
    how a duplicate submission is provoked.
    """

    reservation = Reservation.start(
        reservation_id=reservation_id,
        intent_id=intent.intent_id,
        now=now,
    )
    context = ReservationContext(
        now=now,
        intent=intent,
        approval=None,
        capabilities=harness.provider.capabilities,
        open_reservations_for_intent=0,
    )
    reservation = transition(reservation, ReservationEvent.VALIDATE, context)
    reservation = transition(reservation, ReservationEvent.REQUEST_APPROVAL, context)
    harness.store.save(ReservationRecord(reservation=reservation, intent=intent))
    return harness.service.get(reservation_id)


__all__ = [
    "AFTER_QUOTE",
    "LATER",
    "NOW",
    "FailingLedger",
    "Harness",
    "RecordingStore",
    "SpyProvider",
    "approval_for",
    "build_service",
    "fixture_service",
    "make_intent",
    "make_preferences",
    "manual_quote",
    "offer_for",
    "passenger_refs",
]
