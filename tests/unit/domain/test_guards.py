"""Guards refuse with a stable reason instead of failing open."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from raja_assistant.domain.errors import GuardReason, GuardViolation, ValidationError
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.reservation.events import ReservationEvent, TransitionPayload
from raja_assistant.domain.reservation.guards import (
    APPROVAL_MAX_AGE,
    Approval,
    ProviderCapabilities,
    ReservationContext,
)
from raja_assistant.domain.reservation.states import ReservationState
from raja_assistant.domain.reservation.transitions import transition
from support import (
    NOW,
    build_in_state,
    context_for,
    make_capabilities,
    make_context,
    make_intent,
)


def _awaiting() -> Reservation:
    return build_in_state(ReservationState.AWAITING_APPROVAL)


def _submit(reservation: Reservation, context: ReservationContext) -> None:
    transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)


def test_submission_requires_an_authorized_provider() -> None:
    context = make_context(capabilities=ProviderCapabilities())
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), context)
    assert error.value.reason is GuardReason.PROVIDER_NOT_AUTHORIZED


def test_submission_requires_booking_capability_even_when_authorized() -> None:
    context = make_context(capabilities=make_capabilities(book=False, authorized=True))
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), context)
    assert error.value.reason is GuardReason.PROVIDER_NOT_AUTHORIZED


def test_submission_requires_a_recorded_human_approval() -> None:
    context = make_context(approval=None)
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), context)
    assert error.value.reason is GuardReason.APPROVAL_REQUIRED


def test_submission_refuses_an_approval_for_another_intent() -> None:
    approval = Approval(intent_id="intent-other", approved_by="operator", approved_at=NOW)
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), make_context(approval=approval))
    assert error.value.reason is GuardReason.APPROVAL_INTENT_MISMATCH


def test_submission_refuses_a_stale_approval() -> None:
    approval = Approval(
        intent_id=make_intent().intent_id,
        approved_by="operator",
        approved_at=NOW - APPROVAL_MAX_AGE - timedelta(seconds=1),
    )
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), make_context(approval=approval))
    assert error.value.reason is GuardReason.APPROVAL_STALE


def test_submission_refuses_an_approval_from_the_future() -> None:
    approval = Approval(
        intent_id=make_intent().intent_id,
        approved_by="operator",
        approved_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), make_context(approval=approval))
    assert error.value.reason is GuardReason.APPROVAL_STALE


def test_submission_refuses_an_expired_quote() -> None:
    intent = make_intent()
    context = make_context(now=intent.quote_expires_at, intent=intent)
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), context)
    assert error.value.reason is GuardReason.QUOTE_EXPIRED


def test_submission_refuses_when_another_reservation_is_open() -> None:
    context = make_context(open_reservations_for_intent=1)
    with pytest.raises(GuardViolation) as error:
        _submit(_awaiting(), context)
    assert error.value.reason is GuardReason.DUPLICATE_SUBMISSION_PENDING


def test_clock_regression_is_refused() -> None:
    reservation = _awaiting()
    earlier = make_context(now=NOW - timedelta(seconds=1))
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.APPROVE_AND_SUBMIT, earlier)
    assert error.value.reason is GuardReason.CLOCK_BACKWARDS


def test_missing_intent_blocks_validation() -> None:
    reservation = build_in_state(ReservationState.DRAFT)
    context = ReservationContext(now=NOW, capabilities=make_capabilities())
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.VALIDATE, context)
    assert error.value.reason is GuardReason.INTENT_MISSING


def test_quote_expiry_cannot_be_declared_early() -> None:
    reservation = build_in_state(ReservationState.VALIDATED)
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.QUOTE_EXPIRED, make_context())
    assert error.value.reason is GuardReason.QUOTE_NOT_EXPIRED


def test_confirmation_requires_a_provider_reference() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.ACK_CONFIRMED, make_context())
    assert error.value.reason is GuardReason.PROVIDER_REFERENCE_MISSING


def test_rejection_requires_a_reason() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.ACK_REJECTED, make_context())
    assert error.value.reason is GuardReason.REJECTION_REASON_MISSING


@pytest.mark.parametrize(
    "event",
    [ReservationEvent.VALIDATION_FAILED, ReservationEvent.PAYMENT_FAILED],
)
def test_other_rejection_paths_require_a_reason(event: ReservationEvent) -> None:
    state = (
        ReservationState.DRAFT
        if event is ReservationEvent.VALIDATION_FAILED
        else ReservationState.PAYMENT_PENDING
    )
    reservation = build_in_state(state)
    with pytest.raises(GuardViolation) as error:
        transition(reservation, event, context_for(state, event))
    assert error.value.reason is GuardReason.REJECTION_REASON_MISSING


def test_hold_requires_provider_support() -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    context = make_context(capabilities=make_capabilities(hold=False))
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.ACK_HELD,
            context,
            TransitionPayload(hold_expires_at=NOW + timedelta(hours=1)),
        )
    assert error.value.reason is GuardReason.CAPABILITY_UNSUPPORTED


@pytest.mark.parametrize(
    "hold_expires_at",
    [None, NOW, NOW - timedelta(minutes=1), datetime(2026, 10, 24, 15, 0)],
)
def test_hold_requires_a_future_aware_expiry(hold_expires_at: datetime | None) -> None:
    reservation = build_in_state(ReservationState.SUBMITTING)
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.ACK_HELD,
            make_context(),
            TransitionPayload(hold_expires_at=hold_expires_at),
        )
    assert error.value.reason is GuardReason.HOLD_EXPIRY_INVALID


def test_payment_requires_provider_support() -> None:
    reservation = build_in_state(ReservationState.HELD)
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.PAY,
            make_context(capabilities=make_capabilities(pay=False)),
        )
    assert error.value.reason is GuardReason.CAPABILITY_UNSUPPORTED


def test_hold_cannot_expire_early() -> None:
    reservation = build_in_state(ReservationState.HELD)
    with pytest.raises(GuardViolation) as error:
        transition(reservation, ReservationEvent.HOLD_EXPIRED, make_context())
    assert error.value.reason is GuardReason.HOLD_NOT_EXPIRED


def test_reconciled_booking_requires_a_reference_and_evidence() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.RECONCILE_BOOKED,
            make_context(),
            TransitionPayload(evidence_note="operator checked the provider panel"),
        )
    assert error.value.reason is GuardReason.PROVIDER_REFERENCE_MISSING


def test_reconciled_booking_requires_evidence_for_the_reference() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.RECONCILE_BOOKED,
            make_context(),
            TransitionPayload(provider_reference="PNR-1"),
        )
    assert error.value.reason is GuardReason.EVIDENCE_MISSING


def test_reconciled_absence_requires_evidence() -> None:
    reservation = build_in_state(ReservationState.SUBMISSION_UNKNOWN)
    with pytest.raises(GuardViolation) as error:
        transition(
            reservation,
            ReservationEvent.RECONCILE_NOT_BOOKED,
            make_context(),
            TransitionPayload(),
        )
    assert error.value.reason is GuardReason.EVIDENCE_MISSING


def test_context_rejects_negative_open_reservation_count() -> None:
    with pytest.raises(ValidationError):
        make_context(open_reservations_for_intent=-1)


def test_context_requires_aware_now() -> None:
    with pytest.raises(ValidationError):
        ReservationContext(now=datetime(2026, 10, 24, 12, 0, tzinfo=None))


def test_context_accepts_explicit_utc() -> None:
    context = ReservationContext(now=NOW.astimezone(UTC))
    assert context.capabilities.authorized is False
