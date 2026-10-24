"""The shared mapping is the only translation from provider results to events."""

from __future__ import annotations

from datetime import timedelta

import pytest

from raja_assistant.application.mapping import (
    NOT_BOOKED_EVIDENCE,
    hold_evidence,
    reconciliation_event,
    submission_event,
)
from raja_assistant.application.ports.errors import ProviderContractError
from raja_assistant.application.ports.models import (
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationPending,
    ReservationRejected,
    SearchSucceeded,
    SubmissionOutcomeUnknown,
)
from raja_assistant.domain.reservation.events import ReservationEvent
from support import NOW

EXPIRY = NOW + timedelta(minutes=30)

CONFIRMED = ReservationConfirmed(provider_reference="SIM-0000000001", observed_at=NOW)
HELD = ReservationHeld(
    provider_reference="SIM-0000000001",
    hold_expires_at=EXPIRY,
    observed_at=NOW,
)
PENDING = ReservationPending(client_reference="key-1", observed_at=NOW)
REJECTED = ReservationRejected(reason="sold_out", observed_at=NOW)
UNKNOWN = SubmissionOutcomeUnknown(client_reference="key-1", observed_at=NOW)
UNAVAILABLE = ProviderUnavailable(observed_at=NOW)
NOT_FOUND = ReservationNotFound(client_reference="key-1", observed_at=NOW)

RECONCILE_EVENTS = {
    ReservationEvent.RECONCILE_BOOKED,
    ReservationEvent.RECONCILE_INCONCLUSIVE,
    ReservationEvent.RECONCILE_NOT_BOOKED,
}


@pytest.mark.parametrize(
    ("result", "event"),
    [
        (CONFIRMED, ReservationEvent.ACK_CONFIRMED),
        (HELD, ReservationEvent.ACK_HELD),
        (REJECTED, ReservationEvent.ACK_REJECTED),
        (UNKNOWN, ReservationEvent.ACK_UNKNOWN),
        # D1: a pending answer is ambiguous, never a confirmation.
        (PENDING, ReservationEvent.ACK_UNKNOWN),
        # D5: only the adapter's no-side-effect guarantee licenses this event.
        (UNAVAILABLE, ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT),
    ],
    ids=["confirmed", "held", "rejected", "unknown", "pending", "unavailable"],
)

def test_every_submission_result_maps_to_exactly_one_event(result: object, event: ReservationEvent) -> None:  # noqa: E501
    mapped, payload = submission_event(result)  # type: ignore[arg-type]

    assert mapped is event
    assert payload is not None


def test_a_confirmation_carries_its_reference() -> None:
    _, payload = submission_event(CONFIRMED)

    assert payload.provider_reference == CONFIRMED.provider_reference


def test_a_rejection_carries_the_provider_reason() -> None:
    _, payload = submission_event(REJECTED)

    assert payload.rejection_reason == "sold_out"


def test_an_unusable_submission_result_is_a_contract_error() -> None:
    with pytest.raises(ProviderContractError) as error:
        submission_event(SearchSucceeded(offers=(), observed_at=NOW))  # type: ignore[arg-type]

    assert error.value.field == "result"


@pytest.mark.parametrize(
    ("result", "event"),
    [
        (CONFIRMED, ReservationEvent.RECONCILE_BOOKED),
        (NOT_FOUND, ReservationEvent.RECONCILE_NOT_BOOKED),
        (UNAVAILABLE, ReservationEvent.RECONCILE_INCONCLUSIVE),
        (PENDING, ReservationEvent.RECONCILE_INCONCLUSIVE),
        # D2: a hold discovered by lookup is inconclusive evidence, not a booking.
        (HELD, ReservationEvent.RECONCILE_INCONCLUSIVE),
    ],
    ids=["confirmed", "not_found", "unavailable", "pending", "held"],
)

def test_every_lookup_result_maps_to_a_reconciliation_event(result: object, event: ReservationEvent) -> None:  # noqa: E501
    mapped, _ = reconciliation_event(result)  # type: ignore[arg-type]

    assert mapped is event
    assert mapped in RECONCILE_EVENTS


def test_a_lookup_can_never_produce_a_submission_event() -> None:
    for result in (CONFIRMED, NOT_FOUND, UNAVAILABLE, PENDING, HELD):
        event, _ = reconciliation_event(result)
        assert event not in {
            ReservationEvent.ACK_CONFIRMED,
            ReservationEvent.ACK_HELD,
            ReservationEvent.ACK_REJECTED,
            ReservationEvent.ACK_UNKNOWN,
            ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT,
        }


def test_the_booked_evidence_is_fixed() -> None:
    _, payload = reconciliation_event(CONFIRMED)

    assert payload.evidence_note == "lookup reports the reservation as booked"
    assert payload.provider_reference == CONFIRMED.provider_reference


def test_the_not_booked_evidence_is_fixed() -> None:
    _, payload = reconciliation_event(NOT_FOUND)

    assert payload.evidence_note == NOT_BOOKED_EVIDENCE


def test_a_hold_evidence_preserves_the_expiry_as_text() -> None:
    _, payload = reconciliation_event(HELD)

    assert payload.evidence_note is not None
    assert EXPIRY.isoformat() in payload.evidence_note
    assert "a hold is not a confirmed reservation" in payload.evidence_note
    assert payload.hold_expires_at is None
    assert hold_evidence(EXPIRY) == payload.evidence_note


def test_an_unusable_lookup_result_is_a_contract_error() -> None:
    with pytest.raises(ProviderContractError):
        reconciliation_event(REJECTED)  # type: ignore[arg-type]
