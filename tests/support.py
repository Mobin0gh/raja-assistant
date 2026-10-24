"""Deterministic builders shared by the unit tests.

Every clock value is fixed here. No test reads the wall clock, so failures are
reproducible and time-dependent guards are exercised exactly.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, StationCode, TripPreferences
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.models.service import ServiceId
from raja_assistant.domain.reservation.events import ReservationEvent, TransitionPayload
from raja_assistant.domain.reservation.guards import (
    Approval,
    ProviderCapabilities,
    ReservationContext,
)
from raja_assistant.domain.reservation.states import ReservationState
from raja_assistant.domain.reservation.transitions import transition

TEHRAN = timezone(timedelta(hours=3, minutes=30))
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=TEHRAN)
QUOTE_TTL = timedelta(minutes=15)

THR = StationCode("THR")
MHD = StationCode("MHD")


def make_preferences(**overrides: object) -> TripPreferences:
    base: dict[str, object] = {
        "origin": THR,
        "destination": MHD,
        "travel_date": date(2026, 11, 1),
        "party_size": 2,
        "quota_classes": frozenset({QuotaClass("economy")}),
        "max_fare_irr": 10_000_000,
        "earliest_departure": time(18, 0),
        "latest_departure": time(23, 0),
    }
    base.update(overrides)
    return TripPreferences(**base)  # type: ignore[arg-type]


def make_intent(**overrides: object) -> ReservationIntent:
    base: dict[str, object] = {
        "intent_id": "intent-0001",
        "preferences": make_preferences(),
        "service_id": ServiceId("svc-1001"),
        "quota_class": QuotaClass("economy"),
        "passenger_refs": (PassengerRef("pax-aaaa"), PassengerRef("pax-bbbb")),
        "fare_irr": 4_500_000,
        "quote_expires_at": NOW + QUOTE_TTL,
        "created_at": NOW,
    }
    base.update(overrides)
    return ReservationIntent(**base)  # type: ignore[arg-type]


def make_capabilities(**overrides: object) -> ProviderCapabilities:
    base: dict[str, object] = {
        "authorized": True,
        "book": True,
        "hold": True,
        "pay": True,
        "lookup_by_reference": True,
    }
    base.update(overrides)
    return ProviderCapabilities(**base)  # type: ignore[arg-type]


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<unset>"


UNSET = _Unset()


def make_context(
    *,
    now: datetime = NOW,
    intent: ReservationIntent | _Unset | None = UNSET,
    approval: Approval | _Unset | None = UNSET,
    capabilities: ProviderCapabilities | None = None,
    open_reservations_for_intent: int = 0,
) -> ReservationContext:
    resolved_intent = make_intent() if isinstance(intent, _Unset) else intent
    if isinstance(approval, _Unset):
        resolved_approval: Approval | None = (
            Approval(intent_id=resolved_intent.intent_id, approved_by="operator", approved_at=now)
            if resolved_intent is not None
            else None
        )
    else:
        resolved_approval = approval
    return ReservationContext(
        now=now,
        intent=resolved_intent,
        approval=resolved_approval,
        capabilities=capabilities if capabilities is not None else make_capabilities(),
        open_reservations_for_intent=open_reservations_for_intent,
    )


def new_reservation(*, now: datetime = NOW) -> Reservation:
    return Reservation.start(reservation_id="rsv-0001", intent_id=make_intent().intent_id, now=now)


def build_in_state(state: ReservationState, *, now: datetime = NOW) -> Reservation:
    """Drive a reservation into ``state`` through the real transition table."""

    intent = make_intent()
    context = make_context(now=now, intent=intent)
    reservation = new_reservation(now=now)
    if state is ReservationState.DRAFT:
        return reservation

    reservation = transition(reservation, ReservationEvent.VALIDATE, context)
    if state is ReservationState.VALIDATED:
        return reservation

    reservation = transition(reservation, ReservationEvent.REQUEST_APPROVAL, context)
    if state is ReservationState.AWAITING_APPROVAL:
        return reservation

    if state is ReservationState.CANCELLED:
        return transition(reservation, ReservationEvent.DECLINE, context)

    if state is ReservationState.EXPIRED:
        late = make_context(now=intent.quote_expires_at, intent=intent)
        return transition(reservation, ReservationEvent.QUOTE_EXPIRED, late)

    reservation = transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)
    if state is ReservationState.SUBMITTING:
        return reservation

    if state is ReservationState.SUBMISSION_UNKNOWN:
        return transition(reservation, ReservationEvent.ACK_UNKNOWN, context)

    if state is ReservationState.CONFIRMED:
        return transition(
            reservation,
            ReservationEvent.ACK_CONFIRMED,
            context,
            payload_for(ReservationEvent.ACK_CONFIRMED),
        )

    if state is ReservationState.REJECTED:
        return transition(
            reservation,
            ReservationEvent.ACK_REJECTED,
            context,
            payload_for(ReservationEvent.ACK_REJECTED),
        )

    reservation = transition(
        reservation,
        ReservationEvent.ACK_HELD,
        context,
        payload_for(ReservationEvent.ACK_HELD),
    )
    if state is ReservationState.HELD:
        return reservation

    return transition(reservation, ReservationEvent.PAY, context)


def payload_for(event: ReservationEvent) -> TransitionPayload:
    """Return a payload that satisfies the guards for ``event``."""

    if event in (ReservationEvent.ACK_CONFIRMED, ReservationEvent.PAYMENT_CONFIRMED):
        return TransitionPayload(provider_reference="PNR-TEST-1")
    if event in (
        ReservationEvent.ACK_REJECTED,
        ReservationEvent.PAYMENT_FAILED,
        ReservationEvent.VALIDATION_FAILED,
    ):
        return TransitionPayload(rejection_reason="provider_rejected")
    if event is ReservationEvent.ACK_HELD:
        return TransitionPayload(hold_expires_at=NOW + timedelta(hours=1))
    if event is ReservationEvent.RECONCILE_BOOKED:
        return TransitionPayload(
            provider_reference="PNR-RECONCILED-1",
            evidence_note="provider lookup by client reference",
        )
    if event is ReservationEvent.RECONCILE_NOT_BOOKED:
        return TransitionPayload(evidence_note="provider lookup found no reservation")
    if event is ReservationEvent.RECONCILE_INCONCLUSIVE:
        return TransitionPayload(evidence_note="lookup endpoint unavailable")
    return TransitionPayload()


def context_for(state: ReservationState, event: ReservationEvent) -> ReservationContext:
    """Context that lets a legal edge succeed and keeps illegal edges illegal."""

    intent = make_intent()
    if state in (ReservationState.VALIDATED, ReservationState.AWAITING_APPROVAL) and (
        event is ReservationEvent.QUOTE_EXPIRED
    ):
        return make_context(now=intent.quote_expires_at, intent=intent)
    if state is ReservationState.HELD and event is ReservationEvent.HOLD_EXPIRED:
        return make_context(now=NOW + timedelta(hours=2), intent=intent)
    return make_context(now=NOW, intent=intent)

